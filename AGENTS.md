# Bento Lab Control Library

Python BLE/Wi-Fi control library for Bento Lab PCR workstations
(Bento Bioworks). The BLE protocol was reverse-engineered from HCI snoop
captures and APK decompilation -- no public API or SDK exists.

## Architecture

```text
bentolab/
  ble_client.py      # Async BLE client (bleak) — primary transport
  wifi_client.py     # HTTP/WS client for V1.31 unit (stub, protocol TBD)
  protocol.py        # NUS framing, command/response codec, UUID tables
  models.py          # Domain types: PCRProfile, DeviceState, ThermalStep
  thermocycler.py    # Unified high-level interface (BLE + Wi-Fi)
tools/               # Standalone debug scripts (scanner, commander, monitor)
tests/               # pytest + pytest-asyncio, all mocked (no hardware)
docs/                # Protocol RE findings (GATT profile, commands, firmware)
```

## Target Devices

| Unit | HW Version | Serial  | Transport | Status           |
|------|-----------|---------|-----------|------------------|
| 1    | V1.4      | BL13489 | BLE       | Protocol decoded  |
| 2    | V1.31     | BL13125 | Wi-Fi     | Protocol TBD     |

## Domain Rules

### BLE Protocol

- **Transport**: Nordic UART Service (NUS) over BLE, nRF52840 MCU.
- **Framing**: `_.;<payload>\n\n` for commands; semicolon-delimited responses.
- **Status broadcast**: Device sends `bb;...` every ~5 seconds when connected.
- **Profile upload sequence**: `pb` -> `w` -> stages (`x`) -> cycles (`z`) ->
  lid temp (`A`) -> name (`I`) -> slot (`B`) -> finalize (`B`).
- **macOS caveat**: CoreBluetooth exposes UUIDs only, not GATT handle numbers.

### PCR Thermal Cycling

- Typical profile: initial denaturation -> (denature/anneal/extend) x N -> final extension -> hold.
- Touchdown PCR: annealing temp decreases by `delta` each repeat (`y` command).
- Lid temperature keeps condensation off the tube caps (typically 110 C).
- Stage durations are in seconds; temperatures in Celsius (float).

### Key UUIDs

| UUID (prefix) | Purpose |
|---------------|---------|
| `6e400001-...` | NUS Service |
| `6e400002-...` | NUS RX (write commands to device) |
| `6e400003-...` | NUS TX (notifications from device) |
| `6e409a18-...` | Bento advertising / scan filter |

## Environment

- Python 3.13 (venv at `.venv/`, created via `make setup`).
- Package manager: `uv`. Never use `pip` directly.
- `requires-python = ">=3.11"` — CI matrix covers 3.11/3.12/3.13.

## QA

```bash
make validate        # Full check: format, lint, types, complexity, tests
make lint_fix        # Auto-fix lint + format
make quick_validate  # Ruff + pyright only (skip tests)
make check_complexity # complexipy analysis
make test            # pytest -m "not hardware"
```

Hardware tests (`@pytest.mark.hardware`) are excluded from CI.
Run with `pytest -m hardware` when a Bento Lab is physically connected.

## Key Files

| File | Purpose |
|------|---------|
| `bentolab/protocol.py` | Complete protocol codec (commands, responses, UUIDs) |
| `bentolab/ble_client.py` | Async BLE client with connection mgmt and PCR run |
| `bentolab/models.py` | PCRProfile, DeviceState, ThermalStep dataclasses |
| `tests/test_protocol.py` | Protocol encode/decode tests from HCI capture data |
| `docs/protocol-commands.md` | Full command reference |
| `docs/ble-gatt-profile.md` | GATT service/characteristic map |
