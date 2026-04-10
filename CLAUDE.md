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
<!-- Updated 2026-04-10 from APK analysis (Bento Bio v0.5.4, Flutter) -->

### App Architecture
- **Framework:** Flutter (Dart AOT compiled to libapp.so)
- **BLE library:** flutter_blue_plus
- **DFU library:** Nordic Semiconductor DFU (no.nordicsemi.android.dfu)
- **MCU:** Nordic nRF (confirmed by NUS + Nordic DFU)
- **Developer:** Philipp Boeing (file path in binary: /Users/philippboeing/bento-app/)

### BLE UUIDs (from libapp.so string extraction)
- **NUS Service:** `6e400001-b5a3-f393-e0a9-e50e24dcca9e`
- **NUS RX (write to device):** `6e400002-b5a3-f393-e0a9-e50e24dcca9e`
- **NUS TX (notify from device):** `6e400003-b5a3-f393-e0a9-e50e24dcca9e`
- **Bento Custom 1:** `6e409a18-b5a3-f393-e0a9-e50e24dcca9e`
- **Bento Custom 2:** `6e409a19-b5a3-f393-e0a9-e50e24dcca9e`

### Protocol
- Commands sent via NUS RX, responses via NUS TX notifications
- Key functions: `processBluetoothCommand`, `processBluetoothMessage`
- PCR profiles framed: `pcrProfileBegin` -> data -> `pcrProfileDone`
- Data model: PcrProgram > PcrProgramCycle > PcrProgramStage (JSON)
- Device controls: `updateCentMode`, `updateGelMode`, `sendPcrProfileToRun`

### API & Firmware URLs
- **API base:** `https://api2.bento.bio/`
- **Devices endpoint:** `https://api2.bento.bio/devices/`
- **Firmware images:** `https://api2.bento.bio/static/firmware-images/`
- **Legacy update path:** `bl-legacy-update/`

### Device Types
- `DeviceBentoLab` / `DeviceBentoLabV17` — Bento Lab (our targets)
- `DeviceGoPCR` — separate goPCR companion device
- `BentoLab15` — legacy V1.5 model
