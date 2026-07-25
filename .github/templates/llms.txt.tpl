# ${PROJECT_NAME}

> ${PROJECT_DESC}

## Documentation

- [README](${BLOB}/README.md)
- [BLE GATT Profile](${BLOB}/docs/ble-gatt-profile.md)
- [Protocol Commands](${BLOB}/docs/protocol-commands.md)
- [Firmware Analysis](${BLOB}/docs/firmware-analysis.md)
- [Wi-Fi Protocol](${BLOB}/docs/wifi-protocol.md)
- [CLI Scripting Contract](${BLOB}/docs/cli-scripting.md)

## Source

- [models.py](${BLOB}/bentolab/models.py): Domain types (DeviceStatus, ThermalStep, CycleStep, PCRProfile)
- [protocol.py](${BLOB}/bentolab/protocol.py): Low-level NUS UART encoding/decoding
- [ble_client.py](${BLOB}/bentolab/ble_client.py): Async BLE connectivity + application-layer keep-alive
- [wifi_client.py](${BLOB}/bentolab/wifi_client.py): Wi-Fi connectivity (stub for V1.31 unit)
- [runs.py](${BLOB}/bentolab/runs.py): Unified run lifecycle (RunLifecycle, RunManager)
- [profiles.py](${BLOB}/bentolab/profiles.py): Profile filesystem store (YAML in XDG data dir)
- [devices.py](${BLOB}/bentolab/devices.py): Persistent last-seen device registry
- [cli/](${BLOB}/bentolab/cli/): Typer CLI (`scan`, `status`, `run`, `stop`, `monitor`, `profile`, `token`, `logs`, `serve`)
- [api/](${BLOB}/bentolab/api/): FastAPI HTTP wrapper (C22 contract + token auth + SSE telemetry)
- [tui/](${BLOB}/bentolab/tui/): Textual workbench (opt-in, requires `bentolab[tui]`)

## Tools

- [ble_scanner.py](${BLOB}/tools/ble_scanner.py): BLE device discovery + GATT enumeration
- [ble_commander.py](${BLOB}/tools/ble_commander.py): Interactive BLE command REPL
- [ble_monitor.py](${BLOB}/tools/ble_monitor.py): BLE notification monitor
- [wifi_scanner.py](${BLOB}/tools/wifi_scanner.py): Wi-Fi device discovery
- [wifi_monitor.py](${BLOB}/tools/wifi_monitor.py): Wi-Fi probe capture
- [session_logger.py](${BLOB}/tools/session_logger.py): NDJSON session recording
- [keep_alive_soak.py](${BLOB}/tools/keep_alive_soak.py): 5-min hardware soak for the BLE keep-alive

## Examples

- [elabftw_demo.py](${BLOB}/examples/elabftw_demo.py): End-to-end demo against the real device using the lab-copilot-gateway's HttpBentoLabClient
