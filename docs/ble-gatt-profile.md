# BLE GATT Profile — Bento Lab Pro V1.4

**Status:** PARTIALLY DISCOVERED (from APK analysis, pending live validation)
**Device:** BL13489 (Pro V1.4, BLE)
**Source:** Flutter APK `bio.bento.app` v0.5.4, `libapp.so` string extraction

## Key Finding: Nordic UART Service (NUS)

The Bento Lab uses **Nordic UART Service** as its primary BLE communication channel.
This is a serial-over-BLE protocol — all commands and responses flow through two characteristics
acting as a virtual serial port. The MCU is a **Nordic nRF** chip (confirmed by Nordic DFU library).

## Discovered Services

| Service UUID | Name | Source |
|-------------|------|--------|
| `6e400001-b5a3-f393-e0a9-e50e24dcca9e` | Nordic UART Service (NUS) | APK strings |
| `0000fe59-0000-1000-8000-00805f9b34fb` | Nordic DFU Service | jadx (Nordic DFU library) |
| Standard SIG services (Generic Access, Device Info, etc.) | Expected | TBD |

## Characteristics Table

| Service | Char UUID | Properties | Description |
|---------|-----------|------------|-------------|
| NUS | `6e400002-b5a3-f393-e0a9-e50e24dcca9e` | Write | **NUS RX** — write commands to device |
| NUS | `6e400003-b5a3-f393-e0a9-e50e24dcca9e` | Notify | **NUS TX** — receive responses from device |
| Custom | `6e409a18-b5a3-f393-e0a9-e50e24dcca9e` | TBD | **Bento Custom 1** — purpose unknown |
| Custom | `6e409a19-b5a3-f393-e0a9-e50e24dcca9e` | TBD | **Bento Custom 2** — purpose unknown |
| CCCD | `00002902-0000-1000-8000-00805f9b34fb` | Read/Write | Client Characteristic Config Descriptor |

## Notification Channels

| Char UUID | Data Format | Update Rate | Description |
|-----------|-------------|-------------|-------------|
| `6e400003-...dcca9e` (NUS TX) | TBD (text or binary) | Event-driven | All device responses |
| `6e409a18-...dcca9e` | TBD | TBD | Custom notification (possibly temp/status) |
| `6e409a19-...dcca9e` | TBD | TBD | Custom notification (possibly temp/status) |

## Protocol Architecture

```
App (Flutter)                      Device (Nordic nRF MCU)
    |                                    |
    |-- Write to NUS RX (0x6e400002) --> |  processBluetoothCommand()
    |                                    |
    | <-- Notify on NUS TX (0x6e400003)  |  processBluetoothMessage()
    |                                    |
```

The app uses `flutter_blue_plus` for BLE communication. Key Dart methods:
- `processBluetoothCommand` — sends commands to device
- `processBluetoothMessage` — handles responses from device
- `sendPcrProfileToRun` — sends PCR profile and starts run
- `sendStagesToDevice` — sends individual stages

## PCR Profile Wire Format (Hypothesis)

Based on error messages in the Dart binary:
1. Profile transfer starts with a `pcrProfileBegin` command
2. Stage/cycle data is sent via `addLoadingData` commands
3. Profile transfer ends with a `pcrProfileDone` command
4. Data is JSON-serializable (`PcrProgram.fromJson`, `PcrProgramStage.fromJson`)

## Custom UUIDs Analysis

All Bento UUIDs share the base: `6e40xxxx-b5a3-f393-e0a9-e50e24dcca9e`
- `0001-0003`: Standard Nordic UART Service
- `9a18-9a19`: Bento-specific extensions (may carry real-time telemetry)

## Next Steps

- [ ] Live BLE scan to validate these UUIDs (`tools/ble_scanner.py --connect`)
- [ ] Subscribe to NUS TX + custom chars, interact with device via app, capture traffic
- [ ] Determine if protocol is text-based (JSON) or binary
- [ ] Map the custom characteristics to specific data streams
