#!/usr/bin/env python3
"""Passive Wi-Fi traffic monitor for Bento Lab V1.31.

Captures and logs HTTP/TCP traffic to/from a target IP using tshark/pyshark.
Useful for observing app-device communication and firmware update traffic.

Usage:
    python tools/wifi_monitor.py --target-ip 192.168.1.42
    python tools/wifi_monitor.py --target-ip 192.168.1.42 --duration 300 --live
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()


def check_capture_permissions(interface: str) -> bool:
    """Check if we can capture on the given interface."""
    tshark = shutil.which("tshark")
    if not tshark:
        console.print("[red]tshark not found.[/red]\nInstall Wireshark: brew install wireshark")
        return False

    # Test capture permission
    result = subprocess.run(
        ["tshark", "-i", interface, "-a", "duration:1", "-q"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0 and "permission" in result.stderr.lower():
        console.print(
            f"[red]No capture permission on {interface}.[/red]\n"
            "Options:\n"
            "  1. Run with sudo: sudo python tools/wifi_monitor.py ...\n"
            "  2. Add yourself to the access_bpf group (Wireshark installer does this)\n"
            "  3. Run: sudo chmod o+r /dev/bpf*"
        )
        return False
    return True


def capture_with_tshark(
    interface: str,
    target_ip: str,
    duration: int,
    output_dir: Path,
    live: bool,
) -> None:
    """Capture traffic using tshark directly."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)
    pcap_path = output_dir / f"capture_{timestamp}.pcap"
    json_path = output_dir / f"capture_{timestamp}.json"

    bpf_filter = f"host {target_ip}"
    _print_capture_banner(interface, target_ip, duration, pcap_path)

    tshark_cmd = _build_capture_cmd(interface, bpf_filter, duration, pcap_path)
    packets: list[dict] = []

    if live:
        _run_live_capture(
            tshark_cmd, interface, bpf_filter, duration, target_ip, pcap_path, packets
        )
        _save_packets_json(json_path, target_ip, interface, timestamp, pcap_path, packets)
    else:
        _run_file_only_capture(tshark_cmd, pcap_path)

    _run_post_capture_analysis(pcap_path)


def _print_capture_banner(interface: str, target_ip: str, duration: int, pcap_path: Path) -> None:
    """Print the capture-start banner."""
    console.print(f"[bold]Capturing traffic to/from {target_ip} on {interface}[/bold]")
    console.print(f"[dim]Pcap: {pcap_path}[/dim]")
    if duration > 0:
        console.print(f"[dim]Duration: {duration}s[/dim]")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")


def _build_capture_cmd(
    interface: str, bpf_filter: str, duration: int, pcap_path: Path
) -> list[str]:
    """Build the tshark command that writes a pcap file."""
    cmd = ["tshark", "-i", interface, "-f", bpf_filter, "-w", str(pcap_path)]
    if duration > 0:
        cmd.extend(["-a", f"duration:{duration}"])
    return cmd


_LIVE_DECODE_FIELDS = [
    "frame.time_relative",
    "ip.src",
    "ip.dst",
    "tcp.srcport",
    "tcp.dstport",
    "tcp.len",
    "http.request.method",
    "http.request.uri",
    "http.response.code",
]


def _build_live_cmd(interface: str, bpf_filter: str, duration: int) -> list[str]:
    """Build the tshark command that emits field-separated live decode lines."""
    cmd = [
        "tshark",
        "-i",
        interface,
        "-f",
        bpf_filter,
        "-T",
        "fields",
        "-E",
        "separator=|",
    ]
    for field in _LIVE_DECODE_FIELDS:
        cmd.extend(["-e", field])
    if duration > 0:
        cmd.extend(["-a", f"duration:{duration}"])
    return cmd


def _parse_live_packet(line: str, target_ip: str) -> tuple[str, dict] | None:
    """Parse one tshark field-separator line. Return (display, packet) or None.

    Returns None for empty or short lines.
    """
    parts = line.split("|")
    if len(parts) < 6:
        return None

    time_rel = parts[0][:8] if parts[0] else ""
    src = parts[1] or ""
    dst = parts[2] or ""
    sport = parts[3] or ""
    dport = parts[4] or ""
    tcp_len = parts[5] or "0"
    http_method = parts[6] if len(parts) > 6 else ""
    http_uri = parts[7] if len(parts) > 7 else ""
    http_code = parts[8] if len(parts) > 8 else ""

    http_info = ""
    if http_method:
        http_info = f"{http_method} {http_uri}"
    elif http_code:
        http_info = f"HTTP {http_code}"

    direction = "->" if src != target_ip else "<-"
    display = (
        f"[dim]{time_rel}[/dim] | "
        f"[cyan]{src}[/cyan] {direction} [cyan]{dst}[/cyan] | "
        f"{sport}:{dport} | {tcp_len}B | "
        f"[green]{http_info}[/green]"
    )
    packet = {
        "time": time_rel,
        "src": src,
        "dst": dst,
        "src_port": sport,
        "dst_port": dport,
        "tcp_len": tcp_len,
        "http": http_info,
    }
    return display, packet


def _stream_live_packets(live_proc, target_ip: str, packets: list[dict]) -> None:
    """Read live_proc.stdout, print each parsed line, append to ``packets``."""
    console.print("[dim]Time       | Src -> Dst | Ports | Len | HTTP[/dim]")
    for line in live_proc.stdout:
        line = line.strip()
        if not line:
            continue
        parsed = _parse_live_packet(line, target_ip)
        if parsed is None:
            continue
        display, packet = parsed
        console.print(display)
        packets.append(packet)


def _terminate_pair(*procs) -> None:
    """Send SIGTERM to each subprocess and wait for it to exit."""
    for proc in procs:
        proc.terminate()
        proc.wait()


def _run_live_capture(
    tshark_cmd: list[str],
    interface: str,
    bpf_filter: str,
    duration: int,
    target_ip: str,
    pcap_path: Path,
    packets: list[dict],
) -> None:
    """Run capture-to-file + live-decode in parallel; append to ``packets``."""
    live_cmd = _build_live_cmd(interface, bpf_filter, duration)

    capture_proc = subprocess.Popen(
        tshark_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    live_proc = subprocess.Popen(
        live_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )

    try:
        _stream_live_packets(live_proc, target_ip, packets)
    except KeyboardInterrupt:
        pass
    finally:
        _terminate_pair(live_proc, capture_proc)

    console.print(f"\n[bold]Pcap saved to {pcap_path}[/bold]")


def _run_file_only_capture(tshark_cmd: list[str], pcap_path: Path) -> None:
    """Run tshark to capture to file with no live decode."""
    with contextlib.suppress(KeyboardInterrupt):
        subprocess.run(tshark_cmd, check=True)
    console.print(f"\n[bold]Pcap saved to {pcap_path}[/bold]")
    console.print(f"Analyze with: tshark -r {pcap_path} -Y http")


def _save_packets_json(
    json_path: Path,
    target_ip: str,
    interface: str,
    timestamp: str,
    pcap_path: Path,
    packets: list[dict],
) -> None:
    """Write the packet summary JSON and print the saved-count line."""
    result = {
        "target_ip": target_ip,
        "interface": interface,
        "timestamp": timestamp,
        "packet_count": len(packets),
        "pcap_file": str(pcap_path),
        "packets": packets,
    }
    json_path.write_text(json.dumps(result, indent=2))
    console.print(f"\n[bold]Saved {len(packets)} packets to {json_path}[/bold]")


def _run_post_capture_analysis(pcap_path: Path) -> None:
    """Run tshark for HTTP request and TLS SNI tables; print results."""
    if not (pcap_path.exists() and pcap_path.stat().st_size > 0):
        return
    console.print("\n[bold]Post-capture analysis:[/bold]")
    _print_http_requests(pcap_path)
    _print_tls_server_names(pcap_path)


def _print_http_requests(pcap_path: Path) -> None:
    """Print a table of HTTP requests extracted from the pcap."""
    http_result = subprocess.run(
        [
            "tshark",
            "-r",
            str(pcap_path),
            "-Y",
            "http.request",
            "-T",
            "fields",
            "-e",
            "http.request.method",
            "-e",
            "http.host",
            "-e",
            "http.request.uri",
        ],
        capture_output=True,
        text=True,
    )
    if not http_result.stdout.strip():
        return
    table = Table(title="HTTP Requests")
    table.add_column("Method", style="green")
    table.add_column("Host", style="cyan")
    table.add_column("URI", style="white")
    for line in http_result.stdout.strip().split("\n"):
        parts = line.split("\t")
        if len(parts) >= 3:
            table.add_row(*parts[:3])
    console.print(table)


def _print_tls_server_names(pcap_path: Path) -> None:
    """Print a table of TLS SNI server names extracted from the pcap."""
    tls_result = subprocess.run(
        [
            "tshark",
            "-r",
            str(pcap_path),
            "-Y",
            "tls.handshake.extensions_server_name",
            "-T",
            "fields",
            "-e",
            "ip.dst",
            "-e",
            "tls.handshake.extensions_server_name",
        ],
        capture_output=True,
        text=True,
    )
    if not tls_result.stdout.strip():
        return
    table = Table(title="TLS Server Names (potential firmware update servers)")
    table.add_column("Destination IP", style="cyan")
    table.add_column("Server Name", style="yellow")
    for line in tls_result.stdout.strip().split("\n"):
        parts = line.split("\t")
        if len(parts) >= 2:
            table.add_row(*parts[:2])
    console.print(table)


def main():
    parser = argparse.ArgumentParser(
        description="Passively capture Wi-Fi traffic to/from Bento Lab V1.31"
    )
    parser.add_argument(
        "--target-ip",
        required=True,
        help="IP address of the Bento Lab Wi-Fi unit",
    )
    parser.add_argument(
        "--interface",
        default="en0",
        help="Network interface to capture on (default: en0)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=0,
        help="Capture duration in seconds (0 = until Ctrl+C, default: 0)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("captures/wifi"),
        help="Output directory (default: captures/wifi/)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Show live packet decode in terminal",
    )

    args = parser.parse_args()

    if not check_capture_permissions(args.interface):
        sys.exit(1)

    capture_with_tshark(
        args.interface,
        args.target_ip,
        args.duration,
        args.output_dir,
        args.live,
    )


if __name__ == "__main__":
    main()
