#!/usr/bin/env python3
"""
wifi_scanner.py — IoT device discovery and probing tool for Bento Lab PCR thermocyclers.

Discovers devices on the local network via mDNS/Bonjour and ARP table inspection,
optionally runs nmap port scans and HTTP endpoint probing. Designed for finding
Wi-Fi-connected Bento Lab units (V1.31, serial BL13125) and similar ESP32/STM32-based
IoT devices.

Usage:
    python tools/wifi_scanner.py
    python tools/wifi_scanner.py --subnet 192.168.1.0/24 --port-scan --http-probe
    python tools/wifi_scanner.py --target-ip 192.168.1.42 --http-probe
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import shutil
import socket
import ssl
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from zeroconf import ServiceBrowser, Zeroconf

console = Console()

# ---------------------------------------------------------------------------
# MAC OUI prefixes for common IoT microcontrollers
# ---------------------------------------------------------------------------
INTERESTING_OUIS: dict[str, str] = {
    "24:0a:c4": "Espressif (ESP32)",
    "a4:cf:12": "Espressif (ESP32)",
    "08:3a:8d": "Espressif (ESP32)",
    "30:ae:a4": "Espressif (ESP32)",
    "ac:67:b2": "Espressif (ESP32)",
    "00:80:e1": "STMicroelectronics (STM32)",
    "00:04:a3": "Microchip Technology",
    "00:1a:4d": "Microchip/Atmel",
}

# ---------------------------------------------------------------------------
# mDNS service types to browse
# ---------------------------------------------------------------------------
MDNS_SERVICE_TYPES = [
    "_http._tcp.local.",
    "_ota._tcp.local.",
    "_bentolab._tcp.local.",
]

# ---------------------------------------------------------------------------
# HTTP probing constants
# ---------------------------------------------------------------------------
HTTP_PORTS = [80, 443, 8080, 8266, 5000]
HTTP_PATHS = [
    "/",
    "/version",
    "/info",
    "/status",
    "/api/status",
    "/update",
    "/ota",
    "/firmware",
]
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=3)


# ===== Subnet auto-detection =====


def _detect_subnet_macOS() -> str | None:
    """Detect the local subnet on macOS by inspecting the default route interface."""
    try:
        iface = _get_default_interface()
        if not iface:
            return None
        ip_addr, netmask_hex = _parse_ifconfig_inet(iface)
        if not ip_addr or not netmask_hex:
            return None
        prefix_len = bin(int(netmask_hex, 16)).count("1")
        return str(ipaddress.IPv4Network(f"{ip_addr}/{prefix_len}", strict=False))
    except Exception:
        return None


def _get_default_interface() -> str | None:
    """Return the default-route interface name on macOS, or None on failure."""
    result = subprocess.run(
        ["route", "-n", "get", "default"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("interface:"):
            return stripped.split(":", 1)[1].strip()
    return None


def _parse_ifconfig_inet(iface: str) -> tuple[str | None, str | None]:
    """Read ifconfig output for ``iface``; return (ip, netmask_hex) or (None, None)."""
    result = subprocess.run(
        ["ifconfig", iface],
        capture_output=True,
        text=True,
        timeout=5,
    )
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("inet ") and "broadcast" in stripped:
            parts = stripped.split()
            ip_addr = parts[1]
            netmask_hex = None
            if "netmask" in parts:
                mask_idx = parts.index("netmask") + 1
                if mask_idx < len(parts):
                    netmask_hex = parts[mask_idx]
            return ip_addr, netmask_hex
    return None, None


def _detect_subnet_socket() -> str | None:
    """Fallback subnet detection using a UDP socket to find the local IP."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        # Assume /24 when we can only determine the IP
        network = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
        return str(network)
    except Exception:
        return None


def detect_subnet() -> str:
    """Auto-detect the local subnet CIDR. Tries macOS-specific method first."""
    subnet = _detect_subnet_macOS()
    if subnet:
        return subnet
    subnet = _detect_subnet_socket()
    if subnet:
        return subnet
    # Ultimate fallback
    return "192.168.1.0/24"


# ===== mDNS Discovery =====


class MDNSListener:
    """Collects mDNS service announcements."""

    def __init__(self) -> None:
        self.services: list[dict[str, Any]] = []

    def add_service(self, zc: Zeroconf, svc_type: str, name: str) -> None:
        info = zc.get_service_info(svc_type, name)
        if info is None:
            return
        addresses = [socket.inet_ntoa(addr) for addr in info.addresses if len(addr) == 4]
        props = {}
        if info.properties:
            for k, v in info.properties.items():
                key = k.decode("utf-8", errors="replace") if isinstance(k, bytes) else str(k)
                val = v.decode("utf-8", errors="replace") if isinstance(v, bytes) else str(v)
                props[key] = val

        entry = {
            "service_type": svc_type,
            "name": name,
            "host": info.server,
            "addresses": addresses,
            "port": info.port,
            "properties": props,
        }
        self.services.append(entry)
        console.print(f"  [green]Found[/green] {name} -> {', '.join(addresses)}:{info.port}")

    def remove_service(self, zc: Zeroconf, svc_type: str, name: str) -> None:
        pass

    def update_service(self, zc: Zeroconf, svc_type: str, name: str) -> None:
        pass


async def run_mdns_discovery(browse_time: float) -> list[dict[str, Any]]:
    """Browse mDNS service types for a given duration."""
    console.print(Panel("[bold]mDNS / Bonjour Discovery[/bold]", style="cyan"))
    n_types = len(MDNS_SERVICE_TYPES)
    console.print(f"  Browsing for {browse_time}s across {n_types} service types...")

    zc = Zeroconf()
    listener = MDNSListener()
    browsers = []
    for svc_type in MDNS_SERVICE_TYPES:
        browsers.append(ServiceBrowser(zc, svc_type, listener))

    await asyncio.sleep(browse_time)

    for browser in browsers:
        browser.cancel()
    zc.close()

    if not listener.services:
        console.print("  [dim]No mDNS services found.[/dim]")
    return listener.services


# ===== ARP Table Scan =====


def scan_arp_table() -> list[dict[str, Any]]:
    """Parse the system ARP table and flag interesting OUI prefixes."""
    console.print(Panel("[bold]ARP Table Scan[/bold]", style="cyan"))

    try:
        result = subprocess.run(
            ["arp", "-a"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        console.print("  [yellow]arp command not found.[/yellow]")
        return []

    entries = [_parse_arp_line(line) for line in result.stdout.splitlines()]
    entries = [e for e in entries if e is not None]
    _print_arp_matches(entries)
    _print_arp_summary(entries)
    return entries


def _parse_arp_line(line: str) -> dict[str, Any] | None:
    """Parse one `arp -a` line. Return {ip, mac, oui_match} or None on skip."""
    parts = line.strip().split()
    if len(parts) < 4:
        return None

    ip_str = _extract_ip_token(parts)
    if ip_str is None:
        return None

    mac_str = _extract_mac_token(parts)
    if not mac_str or mac_str == "(incomplete)":
        mac_str = "unknown"

    mac_normalised = _normalise_mac(mac_str)
    oui_prefix = mac_normalised[:8] if mac_normalised != "unknown" else ""
    return {
        "ip": ip_str,
        "mac": mac_normalised,
        "oui_match": INTERESTING_OUIS.get(oui_prefix, ""),
    }


def _extract_ip_token(parts: list[str]) -> str | None:
    """Return the IP address (in parens) from a split arp -a line."""
    for p in parts:
        if p.startswith("(") and p.endswith(")"):
            return p.strip("()")
    return None


def _extract_mac_token(parts: list[str]) -> str | None:
    """Return the MAC address from a split arp -a line, or None if missing.

    Requires at least 2 colons and the token to look like hex.
    """
    for i, p in enumerate(parts):
        if i < 2 or ":" not in p or len(p) < 11:
            continue
        candidate = p.lower().strip()
        if candidate.count(":") < 2:
            continue
        if not all(c in "0123456789abcdef:" for c in candidate):
            continue
        return candidate
    return None


def _normalise_mac(mac: str) -> str:
    """Zero-pad each segment to two hex digits; lowercase. Pass 'unknown' through."""
    if mac == "unknown":
        return "unknown"
    return ":".join(s.zfill(2) for s in mac.split(":")).lower()


def _print_arp_matches(entries: list[dict[str, Any]]) -> None:
    """Print the per-line bold-red match indicators (one per OUI hit)."""
    for e in entries:
        if e["oui_match"]:
            console.print(
                f"  [bold red]** MATCH **[/bold red] {e['ip']}  {e['mac']}  "
                f"[yellow]{e['oui_match']}[/yellow]"
            )


def _print_arp_summary(entries: list[dict[str, Any]]) -> None:
    """Print the ARP entries summary table; 'no entries' fallback otherwise."""
    if not entries:
        console.print("  [dim]No ARP entries found.[/dim]")
        return
    table = Table(title="ARP Entries", show_lines=False)
    table.add_column("IP", style="white")
    table.add_column("MAC", style="dim")
    table.add_column("OUI Match", style="yellow")
    for e in entries:
        style = "bold red" if e["oui_match"] else ""
        table.add_row(e["ip"], e["mac"], e["oui_match"] or "-", style=style)
    console.print(table)


# ===== nmap Port Scan =====


def run_nmap_scan(target: str) -> list[dict[str, Any]]:
    """Run nmap service-version scan on a target. Falls back to TCP connect scan."""
    console.print(Panel(f"[bold]nmap Port Scan: {target}[/bold]", style="cyan"))

    nmap_path = shutil.which("nmap")
    if not nmap_path:
        console.print("  [yellow]nmap not found in PATH. Skipping port scan.[/yellow]")
        return []

    result = _run_nmap_with_fallback(nmap_path, target)
    if result is None:
        return []

    ports = _parse_nmap_output(result.stdout)
    _print_nmap_results(ports, target)
    return ports


def _run_nmap_with_fallback(nmap_path: str, target: str):
    """Run nmap -sV; if it requires root, retry with -sT. Return CompletedProcess or None.

    Returns None when both attempts time out.
    """
    cmd = [nmap_path, "-sV", "-p", "1-10000", target]
    console.print(f"  Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        console.print("  [yellow]nmap timed out (300s limit).[/yellow]")
        return None

    if not _nmap_needs_fallback(result.stderr):
        return result

    console.print("  [yellow]Falling back to TCP connect scan (-sT)...[/yellow]")
    cmd = [nmap_path, "-sT", "-sV", "-p", "1-10000", target]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        console.print("  [yellow]nmap fallback timed out.[/yellow]")
        return None


def _nmap_needs_fallback(stderr: str) -> bool:
    """True if nmap's stderr indicates the scan type requires root and we should retry."""
    if not stderr:
        return False
    lower = stderr.lower()
    return (
        "requires root" in lower
        or "operation not permitted" in lower
        or "you requested a scan type" in lower
    )


def _parse_nmap_output(stdout: str) -> list[dict[str, Any]]:
    """Parse nmap stdout into a list of {port, protocol, service, version} dicts."""
    ports: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if "/tcp" not in line and "/udp" not in line:
            continue
        parts = line.split(None, 3)
        if len(parts) < 3 or parts[1] != "open":
            continue
        port_proto = parts[0]
        service = parts[2] if len(parts) > 2 else ""
        version = parts[3] if len(parts) > 3 else ""
        port_num = int(port_proto.split("/")[0])
        protocol = port_proto.split("/")[1]
        ports.append(
            {
                "port": port_num,
                "protocol": protocol,
                "service": service,
                "version": version.strip(),
            }
        )
    return ports


def _print_nmap_results(ports: list[dict[str, Any]], target: str) -> None:
    """Print the open-ports table; 'no open ports' fallback otherwise."""
    if not ports:
        console.print("  [dim]No open ports found (or scan failed).[/dim]")
        return
    table = Table(title=f"Open Ports on {target}", show_lines=False)
    table.add_column("Port", style="green")
    table.add_column("Protocol")
    table.add_column("Service", style="cyan")
    table.add_column("Version", style="dim")
    for p in ports:
        table.add_row(str(p["port"]), p["protocol"], p["service"], p["version"])
    console.print(table)


# ===== HTTP Endpoint Probing =====


async def probe_http_host(
    host: str,
    ports: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Probe a single host for HTTP endpoints across multiple ports and paths."""
    if ports is None:
        ports = HTTP_PORTS

    # Create a permissive SSL context for self-signed certs
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    results: list[dict[str, Any]] = []

    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT, connector=connector) as session:
        tasks = []
        for port in ports:
            for path in HTTP_PATHS:
                scheme = "https" if port == 443 else "http"
                url = f"{scheme}://{host}:{port}{path}"
                tasks.append(_probe_single_url(session, url, host, port, path))
        gathered = await asyncio.gather(*tasks)
        for r in gathered:
            if r is not None:
                results.append(r)

    return results


async def _probe_single_url(
    session: aiohttp.ClientSession,
    url: str,
    host: str,
    port: int,
    path: str,
) -> dict[str, Any] | None:
    """Attempt a GET request to a single URL and return results or None."""
    try:
        async with session.get(url, allow_redirects=False) as resp:
            body_bytes = await resp.read()
            try:
                body_text = body_bytes.decode("utf-8", errors="replace")
            except Exception:
                body_text = repr(body_bytes[:500])

            headers_dict = {k: v for k, v in resp.headers.items()}
            return {
                "url": url,
                "host": host,
                "port": port,
                "path": path,
                "status": resp.status,
                "headers": headers_dict,
                "server": headers_dict.get("Server", ""),
                "content_type": headers_dict.get("Content-Type", ""),
                "body_snippet": body_text[:500],
            }
    except TimeoutError:
        return None
    except aiohttp.ClientConnectorError:
        return None
    except aiohttp.ClientError:
        return None
    except OSError:
        return None
    except Exception:
        return None


async def run_http_probing(hosts: list[str]) -> list[dict[str, Any]]:
    """Probe a list of hosts for HTTP endpoints."""
    console.print(Panel("[bold]HTTP Endpoint Probing[/bold]", style="cyan"))

    if not hosts:
        console.print("  [dim]No hosts to probe.[/dim]")
        return []

    console.print(
        f"  Probing {len(hosts)} host(s) across {len(HTTP_PORTS)} ports "
        f"and {len(HTTP_PATHS)} paths..."
    )

    all_results: list[dict[str, Any]] = []
    for host in hosts:
        console.print(f"  Scanning [bold]{host}[/bold]...")
        results = await probe_http_host(host)
        all_results.extend(results)
        for r in results:
            console.print(
                f"    [green]{r['status']}[/green] {r['url']}  "
                f"Server={r['server'] or '-'}  "
                f"Type={r['content_type'] or '-'}"
            )

    if all_results:
        table = Table(title="HTTP Endpoints Discovered", show_lines=True)
        table.add_column("URL", style="white", max_width=50)
        table.add_column("Status", style="green")
        table.add_column("Server", style="cyan")
        table.add_column("Content-Type", style="dim")
        table.add_column("Body (first 80 chars)", style="dim", max_width=80)
        for r in all_results:
            snippet = r["body_snippet"][:80].replace("\n", " ").replace("\r", "")
            table.add_row(
                r["url"],
                str(r["status"]),
                r["server"] or "-",
                r["content_type"] or "-",
                snippet,
            )
        console.print(table)
    else:
        console.print("  [dim]No HTTP endpoints responded.[/dim]")

    return all_results


# ===== Results Output =====


def save_results(
    output_dir: Path,
    mdns_services: list[dict[str, Any]],
    arp_entries: list[dict[str, Any]],
    nmap_ports: list[dict[str, Any]],
    http_endpoints: list[dict[str, Any]],
    args: argparse.Namespace,
) -> Path:
    """Save all scan results to a timestamped JSON file."""
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    filepath = output_dir / f"{timestamp}_wifi_scan.json"

    data = {
        "timestamp": datetime.now(UTC).isoformat(),
        "scan_parameters": {
            "subnet": args.subnet,
            "mdns_time": args.mdns_time,
            "port_scan": args.port_scan,
            "http_probe": args.http_probe,
            "target_ip": args.target_ip,
        },
        "mdns_services": mdns_services,
        "arp_entries": arp_entries,
        "nmap_ports": nmap_ports,
        "http_endpoints": http_endpoints,
    }

    filepath.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    console.print(f"\n  [bold green]Results saved to:[/bold green] {filepath}")
    return filepath


def print_summary(
    mdns_services: list[dict[str, Any]],
    arp_entries: list[dict[str, Any]],
    nmap_ports: list[dict[str, Any]],
    http_endpoints: list[dict[str, Any]],
) -> None:
    """Print a final summary panel."""
    arp_matches = [e for e in arp_entries if e.get("oui_match")]

    lines = [
        f"mDNS services discovered: [bold]{len(mdns_services)}[/bold]",
        f"ARP entries total:        [bold]{len(arp_entries)}[/bold]",
        f"ARP IoT OUI matches:      [bold red]{len(arp_matches)}[/bold red]",
        f"Open ports found:         [bold]{len(nmap_ports)}[/bold]",
        f"HTTP endpoints found:     [bold]{len(http_endpoints)}[/bold]",
    ]

    if arp_matches:
        lines.append("")
        lines.append("[bold yellow]Interesting devices:[/bold yellow]")
        for m in arp_matches:
            lines.append(f"  {m['ip']}  {m['mac']}  ({m['oui_match']})")

    console.print(Panel("\n".join(lines), title="Scan Summary", style="bold green"))


# ===== Main =====


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="IoT device discovery tool for Bento Lab PCR thermocyclers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python tools/wifi_scanner.py\n"
            "  python tools/wifi_scanner.py --subnet 192.168.1.0/24 --port-scan --http-probe\n"
            "  python tools/wifi_scanner.py --target-ip 192.168.1.42 --http-probe\n"
        ),
    )
    parser.add_argument(
        "--subnet",
        type=str,
        default=None,
        help="Network CIDR to scan (default: auto-detect from default interface)",
    )
    parser.add_argument(
        "--mdns-time",
        type=float,
        default=5.0,
        help="mDNS browse duration in seconds (default: 5)",
    )
    parser.add_argument(
        "--port-scan",
        action="store_true",
        help="Enable nmap port scan on discovered devices",
    )
    parser.add_argument(
        "--http-probe",
        action="store_true",
        help="Probe discovered hosts for HTTP endpoints",
    )
    parser.add_argument(
        "--target-ip",
        type=str,
        default=None,
        help="Skip discovery, scan a specific IP directly",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="captures/wifi/",
        help="Directory to save results (default: captures/wifi/)",
    )
    return parser


async def async_main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    console.print(
        Panel(
            "[bold]Bento Lab Wi-Fi Scanner[/bold]\nIoT device discovery and probing tool",
            style="bold magenta",
        )
    )

    _resolve_subnet(args)

    discovered_hosts, mdns_services, arp_entries = await _run_discovery_phase(args)
    nmap_ports = await _run_port_scan_phase(discovered_hosts, args)
    http_endpoints = await _run_http_probe_phase(discovered_hosts, args)

    save_results(
        Path(args.output_dir), mdns_services, arp_entries, nmap_ports, http_endpoints, args
    )
    print_summary(mdns_services, arp_entries, nmap_ports, http_endpoints)


def _resolve_subnet(args: argparse.Namespace) -> None:
    """Auto-detect subnet if not given; print a banner either way."""
    if args.subnet is None:
        args.subnet = detect_subnet()
        console.print(f"  Auto-detected subnet: [bold]{args.subnet}[/bold]")
    else:
        console.print(f"  Using subnet: [bold]{args.subnet}[/bold]")


async def _run_discovery_phase(
    args: argparse.Namespace,
) -> tuple[set[str], list[dict[str, Any]], list[dict[str, Any]]]:
    """Run mDNS + ARP discovery (or skip if --target-ip). Return hosts, services, arp."""
    discovered_hosts: set[str] = set()

    if args.target_ip:
        console.print(f"  Target IP specified: [bold]{args.target_ip}[/bold]")
        discovered_hosts.add(args.target_ip)
        return discovered_hosts, [], []

    mdns_services = await run_mdns_discovery(args.mdns_time)
    for svc in mdns_services:
        for addr in svc.get("addresses", []):
            discovered_hosts.add(addr)

    arp_entries = scan_arp_table()
    for entry in arp_entries:
        if entry.get("oui_match"):
            discovered_hosts.add(entry["ip"])

    return discovered_hosts, mdns_services, arp_entries


async def _run_port_scan_phase(
    discovered_hosts: set[str], args: argparse.Namespace
) -> list[dict[str, Any]]:
    """Run nmap on each discovered host if --port-scan was set; [] otherwise."""
    if not args.port_scan:
        return []
    ports: list[dict[str, Any]] = []
    for host in sorted(discovered_hosts):
        ports.extend(run_nmap_scan(host))
    return ports


async def _run_http_probe_phase(
    discovered_hosts: set[str], args: argparse.Namespace
) -> list[dict[str, Any]]:
    """Run HTTP probing on each discovered host if --http-probe was set; [] otherwise."""
    if not args.http_probe:
        return []
    probe_hosts = sorted(discovered_hosts) if discovered_hosts else []
    if not probe_hosts:
        console.print(
            "  [yellow]No hosts discovered for HTTP probing. "
            "Use --target-ip to probe a specific host.[/yellow]"
        )
        return []
    return await run_http_probing(probe_hosts)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
