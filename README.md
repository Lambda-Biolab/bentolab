# Bento Lab Reverse Engineering

Reverse engineering the communication protocols of Bento Lab PCR workstations
(Bento Bioworks, London) to build a Python automation library.

## Target Devices

| Unit | Hardware | Serial | Connectivity | Role |
|------|----------|--------|-------------|------|
| 1 | Pro V1.4 | BL13489 | Bluetooth LE | Primary automation target |
| 2 | Pro V1.31 | BL13125 | Wi-Fi | Secondary / intel gathering |

## Setup

```bash
# One-command setup (requires uv)
make setup

# Or manually:
uv venv --python 3.13
uv pip install -r requirements.txt

# System tools
brew install nmap jadx
```

## Tools

| Tool | Purpose |
|------|---------|
| `tools/apk_strings.py` | Extract BLE UUIDs, commands, URLs from decompiled APK |
| `tools/ble_scanner.py` | Discover BLE devices, enumerate GATT profiles |
| `tools/ble_monitor.py` | Subscribe to all BLE notifications passively |
| `tools/ble_commander.py` | Interactive BLE command REPL with fuzzing |
| `tools/wifi_scanner.py` | mDNS/nmap discovery for Wi-Fi unit |
| `tools/wifi_monitor.py` | Passive TCP traffic capture |

## Quick Start

```bash
# 1. Decompile the Bento Bio APK (highest value step)
jadx -d apk_decompiled/ bento.apk
python tools/apk_strings.py --apk-dir apk_decompiled/

# 2. Scan for BLE devices
python tools/ble_scanner.py --scan-time 10

# 3. Connect and enumerate GATT profile
python tools/ble_scanner.py --connect --device-name "Bento"

# 4. Interactive exploration
python tools/ble_commander.py

# 5. Scan local network for Wi-Fi unit
python tools/wifi_scanner.py
```

## Library Usage (goal)

```python
from bentolab import BentoLabBLE, PCRProfile

async def main():
    lab = BentoLabBLE()
    await lab.connect()

    profile = PCRProfile(
        name="Standard PCR",
        initial_denaturation=(95, 180),
        cycles=35,
        denaturation=(95, 30),
        annealing=(58, 30),
        extension=(72, 60),
        final_extension=(72, 300),
    )

    await lab.start_pcr(profile)
    state = await lab.get_state()
    print(f"Cycle {state.current_cycle}/{state.total_cycles} @ {state.block_temperature}C")
```

## Documentation

- [BLE GATT Profile](docs/ble-gatt-profile.md)
- [Wi-Fi Protocol](docs/wifi-protocol.md)
- [Protocol Commands](docs/protocol-commands.md)
- [Firmware Analysis](docs/firmware-analysis.md)
