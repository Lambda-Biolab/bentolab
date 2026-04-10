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
        # Find the default interface via route
        result = subprocess.run(
            ["route", "-n", "get", "default"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        iface = None
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("interface:"):
                iface = stripped.split(":", 1)[1].strip()
                break
        if not iface:
            return None

        # Parse ifconfig for that interface
        result = subprocess.run(
            ["ifconfig", iface],
            capture_output=True,
            text=True,
            timeout=5,
        )
        ip_addr = None
        netmask_hex = None
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("inet ") and "broadcast" in stripped:
                parts = stripped.split()
                ip_addr = parts[1]
                mask_idx = parts.index("netmask") + 1 if "netmask" in parts else None
                if mask_idx and mask_idx < len(parts):
                    netmask_hex = parts[mask_idx]
                break

        if not ip_addr or not netmask_hex:
            return None

        # Convert hex netmask (0xffffff00) to prefix length
        mask_int = int(netmask_hex, 16)
        prefix_len = bin(mask_int).count("1")
        network = ipaddress.IPv4Network(f"{ip_addr}/{prefix_len}", strict=False)
        return str(network)
    except Exception:
        return None


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

    entries: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        # macOS format: host (ip) at mac on iface [ifscope ...]
        # Linux format: host (ip) at mac [ether] on iface
        parts = line.strip().split()
        if len(parts) < 4:
            continue
        # Extract IP — it sits inside parentheses
        ip_str = None
        mac_str = None
        for i, p in enumerate(parts):
            if p.startswith("(") and p.endswith(")"):
                ip_str = p.strip("()")
            if ":" in p and len(p) >= 11 and i >= 2:
                # Likely a MAC address (at least xx:xx:xx)
                candidate = p.lower().strip()
                if all(c in "0123456789abcdef:" for c in candidate) and candidate.count(":") >= 2:
                    mac_str = candidate

        if not ip_str:
            continue
        if not mac_str or mac_str == "(incomplete)":
            mac_str = "unknown"

        # Normalise MAC segments to two-digit hex for OUI matching
        if mac_str != "unknown":
            segments = mac_str.split(":")
            mac_normalised = ":".join(s.zfill(2) for s in segments).lower()
        else:
            mac_normalised = "unknown"

        oui_prefix = mac_normalised[:8] if mac_normalised != "unknown" else ""
        oui_match = INTERESTING_OUIS.get(oui_prefix, "")

        entry: dict[str, Any] = {
            "ip": ip_str,
            "mac": mac_normalised,
            "oui_match": oui_match,
        }
        entries.append(entry)

        if oui_match:
            console.print(
                f"  [bold red]** MATCH **[/bold red] {ip_str}  {mac_normalised}  "
                f"[yellow]{oui_match}[/yellow]"
            )

    # Print summary table
    if entries:
        table = Table(title="ARP Entries", show_lines=False)
        table.add_column("IP", style="white")
        table.add_column("MAC", style="dim")
        table.add_column("OUI Match", style="yellow")
        for e in entries:
            style = "bold red" if e["oui_match"] else ""
            table.add_row(e["ip"], e["mac"], e["oui_match"] or "-", style=style)
        console.print(table)
    else:
        console.print("  [dim]No ARP entries found.[/dim]")

    return entries


# ===== nmap Port Scan =====


def run_nmap_scan(target: str) -> list[dict[str, Any]]:
    """Run nmap service-version scan on a target. Falls back to TCP connect scan."""
    console.print(Panel(f"[bold]nmap Port Scan: {target}[/bold]", style="cyan"))

    nmap_path = shutil.which("nmap")
    if not nmap_path:
        console.print("  [yellow]nmap not found in PATH. Skipping port scan.[/yellow]")
        return []

    # Try service-version scan; if it needs root, fall back to -sT
    cmd = [nmap_path, "-sV", "-p", "1-10000", target]
    console.print(f"  Running: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        console.print("  [yellow]nmap timed out (300s limit).[/yellow]")
        return []

    # If nmap complains about privileges, retry with -sT (TCP connect)
    if result.returncode != 0 and (
        "requires root" in result.stderr.lower()
        or "operation not permitted" in result.stderr.lower()
        or "you requested a scan type" in result.stderr.lower()
    ):
        console.print("  [yellow]Falling back to TCP connect scan (-sT)...[/yellow]")
        cmd = [nmap_path, "-sT", "-sV", "-p", "1-10000", target]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            console.print("  [yellow]nmap fallback timed out.[/yellow]")
            return []

    # Parse open ports from output
    ports: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        # Lines like: 80/tcp   open  http    Apache httpd 2.4.41
        if "/tcp" in line or "/udp" in line:
            parts = line.split(None, 3)
            if len(parts) >= 3 and parts[1] == "open":
                port_proto = parts[0]  # e.g. "80/tcp"
                service = parts[2] if len(parts) > 2 else ""
                version = parts[3] if len(parts) > 3 else ""
                port_num = int(port_proto.split("/")[0])
                protocol = port_proto.split("/")[1]
                entry = {
                    "port": port_num,
                    "protocol": protocol,
                    "service": service,
                    "version": version.strip(),
                }
                ports.append(entry)

    if ports:
        table = Table(title=f"Open Ports on {target}", show_lines=False)
        table.add_column("Port", style="green")
        table.add_column("Protocol")
        table.add_column("Service", style="cyan")
        table.add_column("Version", style="dim")
        for p in ports:
            table.add_row(str(p["port"]), p["protocol"], p["service"], p["version"])
        console.print(table)
    else:
        console.print("  [dim]No open ports found (or scan failed).[/dim]")

    return ports


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

    # Resolve subnet
    if args.subnet is None:
        args.subnet = detect_subnet()
        console.print(f"  Auto-detected subnet: [bold]{args.subnet}[/bold]")
    else:
        console.print(f"  Using subnet: [bold]{args.subnet}[/bold]")

    # Collect all discovered host IPs for downstream scanning
    discovered_hosts: set[str] = set()

    # If a target IP is specified, skip discovery
    if args.target_ip:
        console.print(f"  Target IP specified: [bold]{args.target_ip}[/bold]")
        discovered_hosts.add(args.target_ip)
        mdns_services: list[dict[str, Any]] = []
        arp_entries: list[dict[str, Any]] = []
    else:
        # Run mDNS discovery
        mdns_services = await run_mdns_discovery(args.mdns_time)
        for svc in mdns_services:
            for addr in svc.get("addresses", []):
                discovered_hosts.add(addr)

        # Run ARP table scan
        arp_entries = scan_arp_table()
        for entry in arp_entries:
            if entry.get("oui_match"):
                discovered_hosts.add(entry["ip"])

    # Port scan (if requested)
    nmap_ports: list[dict[str, Any]] = []
    if args.port_scan:
        for host in sorted(discovered_hosts):
            nmap_ports.extend(run_nmap_scan(host))

    # HTTP probing (if requested)
    http_endpoints: list[dict[str, Any]] = []
    if args.http_probe:
        probe_hosts = sorted(discovered_hosts) if discovered_hosts else []
        # If no hosts discovered but we have a subnet, probe isn't useful without targets
        if not probe_hosts:
            console.print(
                "  [yellow]No hosts discovered for HTTP probing. "
                "Use --target-ip to probe a specific host.[/yellow]"
            )
        else:
            http_endpoints = await run_http_probing(probe_hosts)

    # Save results
    output_dir = Path(args.output_dir)
    save_results(output_dir, mdns_services, arp_entries, nmap_ports, http_endpoints, args)

    # Print summary
    print_summary(mdns_services, arp_entries, nmap_ports, http_endpoints)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
