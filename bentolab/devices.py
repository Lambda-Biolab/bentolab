"""Persistent registry of last-seen Bento Lab devices."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from ._data_dirs import devices_path
from ._store import atomic_write_text, load_with_backup


@dataclass
class Device:
    address: str
    name: str = ""
    transport: str = "ble"  # "ble" or "wifi"
    hw_version: str = ""
    serial: str = ""
    last_seen: str = ""  # ISO-8601 UTC


def _load_raw(path: Path | None = None) -> dict[str, dict]:
    target = path or devices_path()
    data, _source = load_with_backup(target)
    if not data:
        return {}
    try:
        parsed = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): dict(v) for k, v in parsed.items() if isinstance(v, dict)}


def list_devices(*, path: Path | None = None) -> list[Device]:
    raw = _load_raw(path)
    return [Device(**v) for v in raw.values()]


def remember(device: Device, *, path: Path | None = None) -> None:
    target = path or devices_path()
    raw = _load_raw(target)
    device.last_seen = datetime.now(tz=UTC).isoformat()
    raw[device.address] = asdict(device)
    atomic_write_text(target, json.dumps(raw, indent=2, sort_keys=True))


def forget(address: str, *, path: Path | None = None) -> None:
    target = path or devices_path()
    raw = _load_raw(target)
    raw.pop(address, None)
    atomic_write_text(target, json.dumps(raw, indent=2, sort_keys=True))
