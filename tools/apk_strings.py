#!/usr/bin/env python3
"""Extract reverse-engineering-relevant strings from a decompiled Android APK.

Searches jadx output for BLE UUIDs, command constants, byte arrays, URLs,
and protocol-related patterns. Designed for the Bento Lab reverse engineering
project but works on any Android APK.

Usage:
    python tools/apk_strings.py --apk-dir apk_decompiled/
    python tools/apk_strings.py --decompile BentoBio.apk --react-native
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Import SIG UUID lookup tables from the project protocol module.
# We add the project root to sys.path so this works when invoked standalone.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from bentolab.protocol import SIG_CHARACTERISTICS, SIG_SERVICES  # noqa: E402

console = Console()

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------
UUID_128 = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
UUID_16 = re.compile(r"0x[0-9a-fA-F]{4}")
BYTE_ARRAY_JAVA = re.compile(r"new byte\[\]\s*\{([^}]+)\}")
BYTE_ARRAY_KOTLIN = re.compile(r"byteArrayOf\(([^)]+)\)")
URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
IP_PATTERN = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
BLE_WRITE = re.compile(r"writeCharacteristic|writeValue|gatt\.write|BluetoothGattCharacteristic")
COMMAND_CONST = re.compile(r"(?:CMD_|COMMAND_|OP_|OPCODE_)[A-Z_]+\s*=\s*[^;]+")
SERVICE_CHAR_FIELD = re.compile(r"""(?:SERVICE|CHAR|CHARACTERISTIC|UUID)[_A-Z]*\s*=\s*["'][^"']+""")
BENTO_SPECIFIC = re.compile(
    r"[Bb]ento|[Pp][Cc][Rr]|thermocycler|temperature|centrifuge|transilluminator"
)
BLE_CONTEXT_KEYWORDS = re.compile(
    r"bluetooth|ble|gatt|characteristic|service|uuid|BluetoothGatt",
    re.IGNORECASE,
)

PATTERNS: dict[str, re.Pattern[str]] = {
    "ble_uuid_128": UUID_128,
    "byte_array_java": BYTE_ARRAY_JAVA,
    "byte_array_kotlin": BYTE_ARRAY_KOTLIN,
    "url": URL_PATTERN,
    "ip_address": IP_PATTERN,
    "ble_write_call": BLE_WRITE,
    "command_constant": COMMAND_CONST,
    "service_char_field": SERVICE_CHAR_FIELD,
    "bento_specific": BENTO_SPECIFIC,
}


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------
@dataclass
class Match:
    pattern_name: str
    value: str
    file: str
    line_number: int
    line_context: str


@dataclass
class AnalysisResult:
    framework: str = "Native (Java/Kotlin)"
    matches: list[Match] = field(default_factory=list)
    uuid_classifications: dict[str, str] = field(default_factory=dict)
    uuid_groups: dict[str, list[str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Framework detection
# ---------------------------------------------------------------------------
def detect_framework(apk_dir: Path) -> str:
    """Detect whether the APK uses React Native, Flutter, or native code."""
    bundle = apk_dir / "assets" / "index.android.bundle"
    if bundle.exists():
        return "React Native"

    flutter_libs = list(apk_dir.glob("lib/*/libflutter.so"))
    if flutter_libs:
        return "Flutter"

    return "Native (Java/Kotlin)"


# ---------------------------------------------------------------------------
# File scanning
# ---------------------------------------------------------------------------
def iter_source_files(apk_dir: Path) -> list[Path]:
    """Collect all .java, .kt, and .smali files under the decompiled directory."""
    extensions = {".java", ".kt", ".smali"}
    files: list[Path] = []
    for ext in extensions:
        files.extend(apk_dir.rglob(f"*{ext}"))
    return sorted(files)


def scan_file(filepath: Path, apk_dir: Path) -> list[Match]:
    """Scan a single source file for all patterns."""
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    lines = text.splitlines()
    results: list[Match] = []
    rel_path = str(filepath.relative_to(apk_dir))

    # Decide whether 16-bit UUIDs are relevant for this file.
    file_has_ble_context = bool(BLE_CONTEXT_KEYWORDS.search(text))

    for line_num, line in enumerate(lines, start=1):
        for name, pattern in PATTERNS.items():
            for m in pattern.finditer(line):
                results.append(
                    Match(
                        pattern_name=name,
                        value=m.group(0).strip(),
                        file=rel_path,
                        line_number=line_num,
                        line_context=line.strip(),
                    )
                )

        # 16-bit UUIDs only in BLE-related files.
        if file_has_ble_context:
            for m in UUID_16.finditer(line):
                results.append(
                    Match(
                        pattern_name="ble_uuid_16",
                        value=m.group(0),
                        file=rel_path,
                        line_number=line_num,
                        line_context=line.strip(),
                    )
                )

    return results


def scan_js_bundle(bundle_path: Path, apk_dir: Path) -> list[Match]:
    """Scan a React Native JS bundle for patterns."""
    try:
        text = bundle_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        console.print(f"[yellow]Warning: could not read {bundle_path}[/yellow]")
        return []

    # Basic prettification: insert newlines after semicolons and braces for readability.
    if text.count("\n") < len(text) // 500:
        text = re.sub(r";(?=[^\n])", ";\n", text)
        text = re.sub(r"\{(?=[^\n])", "{\n", text)
        text = re.sub(r"\}(?=[^\n])", "}\n", text)

    lines = text.splitlines()
    results: list[Match] = []
    rel_path = str(bundle_path.relative_to(apk_dir))

    for line_num, line in enumerate(lines, start=1):
        for name, pattern in PATTERNS.items():
            for m in pattern.finditer(line):
                results.append(
                    Match(
                        pattern_name=name,
                        value=m.group(0).strip(),
                        file=rel_path,
                        line_number=line_num,
                        line_context=line.strip()[:200],
                    )
                )

        # Always check 16-bit UUIDs in the JS bundle.
        for m in UUID_16.finditer(line):
            results.append(
                Match(
                    pattern_name="ble_uuid_16",
                    value=m.group(0),
                    file=rel_path,
                    line_number=line_num,
                    line_context=line.strip()[:200],
                )
            )

    return results


# ---------------------------------------------------------------------------
# UUID classification
# ---------------------------------------------------------------------------
SIG_BASE_SUFFIX = "-0000-1000-8000-00805f9b34fb"


def classify_uuid(uuid: str) -> str:
    """Classify a 128-bit UUID as SIG service, SIG characteristic, or custom."""
    lower = uuid.lower()
    if lower in SIG_SERVICES:
        return f"SIG Service: {SIG_SERVICES[lower]}"
    if lower in SIG_CHARACTERISTICS:
        return f"SIG Characteristic: {SIG_CHARACTERISTICS[lower]}"
    if lower.endswith(SIG_BASE_SUFFIX):
        short = lower[:8].lstrip("0") or "0"
        return f"SIG (0x{short.upper()}) - unknown assigned number"
    return "Custom"


def base_uuid(uuid: str) -> str:
    """Extract the base UUID (last 96 bits) for grouping related UUIDs."""
    parts = uuid.lower().split("-")
    if len(parts) == 5:
        return f"XXXXXXXX-{'-'.join(parts[1:])}"
    return uuid.lower()


def classify_service_or_char(match: Match) -> str:
    """Guess whether a UUID is used as a service or characteristic from context."""
    ctx = match.line_context.upper()
    if any(kw in ctx for kw in ("SERVICE", "SVC", "PRIMARY")):
        return "service"
    if any(kw in ctx for kw in ("CHAR", "CHARACTERISTIC", "WRITE", "READ", "NOTIFY")):
        return "characteristic"
    return "unknown"


def build_uuid_analysis(
    matches: list[Match],
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Classify and group all 128-bit UUIDs found in matches."""
    classifications: dict[str, str] = {}
    groups: dict[str, list[str]] = {}

    uuids_seen: set[str] = set()
    for m in matches:
        if m.pattern_name != "ble_uuid_128":
            continue
        uuid = m.value.lower()
        if uuid in uuids_seen:
            continue
        uuids_seen.add(uuid)

        classification = classify_uuid(uuid)
        role = classify_service_or_char(m)
        classifications[uuid] = f"{classification} ({role})"

        group_key = base_uuid(uuid)
        groups.setdefault(group_key, []).append(uuid)

    return classifications, groups


# ---------------------------------------------------------------------------
# Decompilation
# ---------------------------------------------------------------------------
def run_jadx(apk_path: Path, output_dir: Path) -> Path:
    """Run jadx to decompile an APK."""
    jadx_bin = shutil.which("jadx")
    if jadx_bin is None:
        console.print(
            "[red]Error: jadx not found in PATH. Install it: https://github.com/skylot/jadx[/red]"
        )
        sys.exit(1)

    dest = output_dir / (apk_path.stem + "_decompiled")
    console.print(f"[cyan]Running jadx on {apk_path} -> {dest}[/cyan]")

    try:
        subprocess.run(
            [jadx_bin, "--output-dir", str(dest), str(apk_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]jadx failed:\n{exc.stderr}[/red]")
        sys.exit(1)

    console.print(f"[green]Decompilation complete: {dest}[/green]")
    return dest


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def deduplicate_matches(matches: list[Match]) -> list[Match]:
    """Remove duplicate (pattern, value) pairs, keeping first occurrence."""
    seen: set[tuple[str, str]] = set()
    result: list[Match] = []
    for m in matches:
        key = (m.pattern_name, m.value)
        if key not in seen:
            seen.add(key)
            result.append(m)
    return result


PATTERN_LABELS: dict[str, str] = {
    "ble_uuid_128": "BLE UUIDs (128-bit)",
    "ble_uuid_16": "BLE UUIDs (16-bit)",
    "byte_array_java": "Byte Arrays (Java)",
    "byte_array_kotlin": "Byte Arrays (Kotlin)",
    "url": "URLs",
    "ip_address": "IP Addresses",
    "ble_write_call": "BLE Write Calls",
    "command_constant": "Command Constants",
    "service_char_field": "Service/Characteristic Fields",
    "bento_specific": "Bento-Specific References",
}


def print_results(result: AnalysisResult, verbose: bool) -> None:
    """Pretty-print the analysis results using rich."""
    console.print()
    console.print(
        Panel(
            f"[bold]Framework:[/bold] {result.framework}",
            title="APK Analysis Results",
            border_style="cyan",
        )
    )

    # Group matches by pattern.
    by_pattern: dict[str, list[Match]] = {}
    for m in result.matches:
        by_pattern.setdefault(m.pattern_name, []).append(m)

    for pattern_name, label in PATTERN_LABELS.items():
        group = by_pattern.get(pattern_name, [])
        if not group:
            continue

        table = Table(title=label, show_lines=False, border_style="dim")
        table.add_column("Value", style="green", max_width=60)
        table.add_column("File", style="blue", max_width=50)
        if verbose:
            table.add_column("Line", style="dim", justify="right")
            table.add_column("Context", style="dim", max_width=80)

        displayed = group if verbose else deduplicate_matches(group)
        for m in displayed:
            if verbose:
                table.add_row(m.value, m.file, str(m.line_number), m.line_context[:80])
            else:
                table.add_row(m.value, m.file)

        console.print(table)
        console.print()

    # UUID classification table.
    if result.uuid_classifications:
        table = Table(title="UUID Classification", show_lines=False, border_style="dim")
        table.add_column("UUID", style="green")
        table.add_column("Classification", style="yellow")
        for uuid, classification in sorted(result.uuid_classifications.items()):
            table.add_row(uuid, classification)
        console.print(table)
        console.print()

    # UUID grouping.
    if result.uuid_groups:
        groups_with_multiple = {k: v for k, v in result.uuid_groups.items() if len(v) > 1}
        if groups_with_multiple:
            table = Table(title="UUID Groups (shared base)", show_lines=True, border_style="dim")
            table.add_column("Base UUID", style="cyan")
            table.add_column("Members", style="green")
            for base, members in sorted(groups_with_multiple.items()):
                table.add_row(base, "\n".join(sorted(members)))
            console.print(table)
            console.print()

    # Summary.
    unique_uuids = len(result.uuid_classifications)
    unique_urls = len({m.value for m in result.matches if m.pattern_name == "url"})
    cmd_consts = len({m.value for m in result.matches if m.pattern_name == "command_constant"})
    bento_refs = len({m.value for m in result.matches if m.pattern_name == "bento_specific"})

    summary_lines = [
        f"[bold]Framework:[/bold]           {result.framework}",
        f"[bold]128-bit UUIDs found:[/bold] {unique_uuids}",
        f"[bold]URLs found:[/bold]          {unique_urls}",
        f"[bold]Command constants:[/bold]   {cmd_consts}",
        f"[bold]Bento references:[/bold]    {bento_refs}",
        f"[bold]Total matches:[/bold]       {len(result.matches)}",
    ]
    console.print(
        Panel(
            "\n".join(summary_lines),
            title="Summary",
            border_style="green",
        )
    )


def save_json(result: AnalysisResult, output_path: Path) -> None:
    """Save the analysis result as structured JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "framework": result.framework,
        "uuid_classifications": result.uuid_classifications,
        "uuid_groups": result.uuid_groups,
        "matches_by_type": {},
        "summary": {
            "total_matches": len(result.matches),
            "unique_uuids_128": len(result.uuid_classifications),
            "unique_urls": len({m.value for m in result.matches if m.pattern_name == "url"}),
            "command_constants": len(
                {m.value for m in result.matches if m.pattern_name == "command_constant"}
            ),
        },
    }

    for m in result.matches:
        bucket = data["matches_by_type"].setdefault(m.pattern_name, [])
        bucket.append(
            {
                "value": m.value,
                "file": m.file,
                "line": m.line_number,
                "context": m.line_context[:200],
            }
        )

    output_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    console.print(f"[green]Results saved to {output_path}[/green]")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract reverse-engineering-relevant strings from a decompiled APK."
    )
    parser.add_argument(
        "--apk-dir",
        type=Path,
        help="Path to jadx decompiled output directory.",
    )
    parser.add_argument(
        "--decompile",
        type=Path,
        metavar="APK_PATH",
        help="Run jadx on this APK first, then analyze the output.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("captures"),
        help="Directory for output files (default: captures/).",
    )
    parser.add_argument(
        "--react-native",
        action="store_true",
        help="Also extract and analyze index.android.bundle if found.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show all matches with file context and line numbers.",
    )

    args = parser.parse_args()

    # Determine the APK directory.
    apk_dir: Path
    if args.decompile:
        if not args.decompile.is_file():
            console.print(f"[red]Error: APK file not found: {args.decompile}[/red]")
            sys.exit(1)
        apk_dir = run_jadx(args.decompile, args.output_dir)
    elif args.apk_dir:
        apk_dir = args.apk_dir.resolve()
    else:
        console.print("[red]Error: either --apk-dir or --decompile is required.[/red]")
        sys.exit(1)

    if not apk_dir.is_dir():
        console.print(f"[red]Error: directory not found: {apk_dir}[/red]")
        sys.exit(1)

    # Detect framework.
    framework = detect_framework(apk_dir)
    console.print(f"[cyan]Detected framework: {framework}[/cyan]")

    # Scan source files.
    source_files = iter_source_files(apk_dir)
    console.print(f"[cyan]Scanning {len(source_files)} source files...[/cyan]")

    all_matches: list[Match] = []
    for filepath in source_files:
        all_matches.extend(scan_file(filepath, apk_dir))

    # React Native bundle analysis.
    scan_rn = args.react_native or framework == "React Native"
    if scan_rn:
        bundle_path = apk_dir / "assets" / "index.android.bundle"
        if bundle_path.exists():
            console.print("[cyan]Scanning React Native bundle...[/cyan]")
            all_matches.extend(scan_js_bundle(bundle_path, apk_dir))
        else:
            console.print(
                "[yellow]Warning: --react-native specified but "
                "assets/index.android.bundle not found.[/yellow]"
            )

    # Classify UUIDs.
    classifications, groups = build_uuid_analysis(all_matches)

    result = AnalysisResult(
        framework=framework,
        matches=all_matches,
        uuid_classifications=classifications,
        uuid_groups=groups,
    )

    # Output.
    print_results(result, verbose=args.verbose)
    output_file = args.output_dir / "apk_analysis.json"
    save_json(result, output_file)


if __name__ == "__main__":
    main()
