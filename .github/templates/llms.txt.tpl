# ${PROJECT_NAME}

> ${PROJECT_DESC}

## Documentation

- [README](${BLOB}/README.md)
- [BLE GATT Profile](${BLOB}/docs/ble-gatt-profile.md)
- [Protocol Commands](${BLOB}/docs/protocol-commands.md)
- [Firmware Analysis](${BLOB}/docs/firmware-analysis.md)
- [Wi-Fi Protocol](${BLOB}/docs/wifi-protocol.md)

## Source

- [ble_client.py](${BLOB}/bentolab/ble_client.py): Async BLE connectivity & command protocol
- [models.py](${BLOB}/bentolab/models.py): Data models (DeviceStatus, ThermalStep, PCRProfile)
- [protocol.py](${BLOB}/bentolab/protocol.py): Low-level NUS UART encoding/decoding
- [thermocycler.py](${BLOB}/bentolab/thermocycler.py): Unified high-level interface
- [wifi_client.py](${BLOB}/bentolab/wifi_client.py): Wi-Fi connectivity

## Tools

- [ble_scanner.py](${BLOB}/tools/ble_scanner.py): Device discovery & GATT enumeration
- [ble_commander.py](${BLOB}/tools/ble_commander.py): Interactive BLE command REPL
- [session_logger.py](${BLOB}/tools/session_logger.py): JSONL session recording
