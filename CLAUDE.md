# Bento Lab Reverse Engineering Project

## Environment
- Python 3.13 via Homebrew, venv at `.venv/`
- Package manager: `uv` (never use pip)
- Setup: `make setup` or `uv venv --python 3.13 && uv pip install -r requirements.txt`
- Run tests: `make test` or `.venv/bin/pytest tests/ -v`
- Lint: `make lint` / Format: `make format`

## Architecture
- `tools/` — Standalone reverse engineering scripts (run directly: `python tools/ble_scanner.py`)
- `bentolab/` — Reusable Python library for controlling Bento Lab units
- `docs/` — Reverse engineering findings (populated as we discover the protocol)
- `captures/` — Raw data captures (JSON committed, pcap/pcapng gitignored)
- `firmware/` — Firmware binaries (gitignored)

## Target Devices
- **Unit 1 (V1.4, serial BL13489)**: BLE-controlled, primary automation target
- **Unit 2 (V1.31, serial BL13125)**: Wi-Fi-connected, secondary/intel target

## Key Constraints
- All BLE code is async (bleak library, uses asyncio)
- macOS CoreBluetooth only exposes UUIDs, NOT GATT handle numbers
- The official app is "Bento Bio" (Android: bio.bento.app)
- No public API or SDK exists — everything is reverse-engineered

## Discovered Protocol Information
<!-- Updated as reverse engineering progresses -->

### BLE UUIDs
_Not yet discovered. Run `tools/apk_strings.py` on decompiled APK first._

### Command Bytes
_Not yet discovered._

### Wi-Fi Endpoints
_Not yet discovered._

### Firmware Update URLs
_Not yet discovered._
