# Bento Lab Control Library

## Environment
- Python 3.13 via Homebrew, venv at `.venv/`
- Package manager: `uv` (never use pip)
- Setup: `make setup` or `uv venv --python 3.13 && uv pip install -r requirements.txt`
- Run tests: `make test` or `.venv/bin/pytest tests/ -v`
- Lint: `make lint` / Format: `make format`

## Architecture
- `bentolab/` — Python library for controlling Bento Lab units
- `tools/` — Standalone scripts for device discovery, monitoring, and debugging
- `docs/` — Protocol documentation and reverse engineering findings
- `tests/` — Test suite

## Target Devices
- **Unit 1 (V1.4, serial BL13489)**: BLE-controlled, primary automation target
- **Unit 2 (V1.31, serial BL13125)**: Wi-Fi-connected, protocol TBD

## Key Constraints
- All BLE code is async (bleak library, uses asyncio)
- macOS CoreBluetooth only exposes UUIDs, NOT GATT handle numbers
- The official app is "Bento Bio" (Android: bio.bento.app)
- No public API or SDK exists — protocol is reverse-engineered

## Protocol Information

### Hardware
- **MCU:** Nordic nRF52840 (ARM Cortex-M4F)
- **BLE Stack:** Nordic SoftDevice
- **App Framework:** Flutter (Dart AOT compiled to libapp.so)
- **BLE library:** flutter_blue_plus
- **DFU library:** Nordic Semiconductor DFU (no.nordicsemi.android.dfu)

### BLE UUIDs
- **NUS Service:** `6e400001-b5a3-f393-e0a9-e50e24dcca9e`
- **NUS RX (write to device):** `6e400002-b5a3-f393-e0a9-e50e24dcca9e`
- **NUS TX (notify from device):** `6e400003-b5a3-f393-e0a9-e50e24dcca9e`
- **Bento Custom 1:** `6e409a18-b5a3-f393-e0a9-e50e24dcca9e`
- **Bento Custom 2:** `6e409a19-b5a3-f393-e0a9-e50e24dcca9e`

### Protocol
- Commands sent via NUS RX, responses via NUS TX notifications
- PCR profiles framed: `pcrProfileBegin` -> data -> `pcrProfileDone`
- Data model: PcrProgram > PcrProgramCycle > PcrProgramStage (JSON)
- Device controls: `updateCentMode`, `updateGelMode`, `sendPcrProfileToRun`

### API & Firmware URLs
- **API base:** `https://api2.bento.bio/`
- **Firmware images:** `https://api2.bento.bio/static/firmware-images/`
- **Note:** Bento Lab firmware files (dfu-1.15.zip, dfu-2.2.zip) have been removed from the server. Only goPCR firmware (bg-p000-1.zip) remains available.

### Device Types
- `DeviceBentoLab` / `DeviceBentoLabV17` — Bento Lab (our targets)
- `DeviceGoPCR` — separate goPCR companion device
- `BentoLab15` — legacy V1.5 model
