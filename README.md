# bentolab

Python control library for [Bento Lab](https://bento.bio/) PCR workstations
(Bento Bioworks, London). Communicates over Bluetooth LE using the Nordic UART
Service protocol, reverse-engineered from the official Bento Bio app.

## Target Devices

| Unit | Hardware | Serial | Connectivity | Status |
|------|----------|--------|-------------|--------|
| 1 | Pro V1.4 | BL13489 | Bluetooth LE | Primary, fully supported |
| 2 | Pro V1.31 | BL13125 | Wi-Fi | Protocol TBD |

## Setup

```bash
# One-command setup (requires uv)
make setup

# Or manually:
uv venv --python 3.13
uv pip install -r requirements.txt
```

## Library Usage

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

## Tools

| Tool | Purpose |
|------|---------|
| `tools/ble_scanner.py` | Discover BLE devices, enumerate GATT profiles |
| `tools/ble_monitor.py` | Subscribe to all BLE notifications passively |
| `tools/ble_commander.py` | Interactive BLE command REPL with fuzzing |
| `tools/wifi_scanner.py` | mDNS/nmap discovery for Wi-Fi unit |
| `tools/wifi_monitor.py` | Passive TCP traffic capture |
| `tools/session_logger.py` | Record BLE sessions to JSONL |

## Quick Start

```bash
# Scan for BLE devices
python tools/ble_scanner.py --scan-time 10

# Connect and enumerate GATT profile
python tools/ble_scanner.py --connect --device-name "Bento"

# Interactive exploration
python tools/ble_commander.py
```

## Documentation

- [BLE GATT Profile](docs/ble-gatt-profile.md)
- [Protocol Commands](docs/protocol-commands.md)
- [Firmware Analysis](docs/firmware-analysis.md)
- [Wi-Fi Protocol](docs/wifi-protocol.md)

## License

[MIT](LICENSE).

## Disclaimer

Not affiliated with, endorsed by, or sponsored by Bento Bioworks Ltd.
"Bento Lab" is a trademark of Bento Bioworks. Protocol information in this
repository was determined through interoperability analysis of BLE
communication with devices owned by the author, consistent with DMCA §1201(f)
(US) and Article 6 of the EU Software Directive 2009/24/EC.
