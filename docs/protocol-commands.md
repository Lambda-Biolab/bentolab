# Protocol Command Reference

**Status:** PARTIALLY DISCOVERED (APK + firmware string extraction, pending live validation)
**Sources:** `libapp.so` (Dart AOT), `nrf52840_xxaa.bin` (firmware binary), jadx output

## Architecture Overview

Communication uses **Nordic UART Service (NUS)** as a virtual serial port:
- App writes commands to NUS RX (`6e400002-...`)
- Device sends responses via NUS TX notifications (`6e400003-...`)
- Protocol appears to be **text-based** (serial-style commands, not binary opcodes)
- Key firmware functions: `handle_command()`, `processBluetoothCommand()`, `blueserial_writechar()`

## Command Format

Commands are sent as text strings over BLE NUS. The firmware parses them via `handle_command()`
using a state machine with states like:
- `CMDHANDLER_SET_PROFILE_EXPECT_ID`
- `CMDHANDLER_SET_PROFILE_EXPECT_SET_NAME_ID`
- `CMDHANDLER_SET_PROFILE_EXPECT_STAGE_TYPE`

The PCR profile is parsed via `fromSerial` (firmware log: `PCR program fromSerial: %s`).

## Known Commands (from firmware + APK strings)

### PCR Profile Management

| Command | Direction | Description |
|---------|-----------|-------------|
| `PCR_SET_PROFILE` | App -> Device | Begin setting a PCR profile |
| `PCR_REQUEST_PROFILE <n>` | App -> Device | Request profile #n from device storage |
| `PCR_LIST_NUM` | App -> Device | Request number of stored profiles |
| `PCR_PROFILE_CHECK_NAME` | App -> Device | Check if a profile name exists |
| `PCR_PROFILE_GET_DUPLICATE_NAME <n>` | App -> Device | Get a unique name for duplicate |
| `pcrProfileBegin` | App -> Device | Start profile data transfer (framing) |
| `pcrProfileDone` | App -> Device | End profile data transfer (framing) |
| `addLoadingData` | App -> Device | Send incremental profile data |

### PCR Profile Responses

| Response | Direction | Description |
|----------|-----------|-------------|
| `PCR_REQUEST_PROFILE <n> OK` | Device -> App | Profile loaded successfully |
| `PCR_REQUEST_PROFILE <n> FAILED!` | Device -> App | Profile load failed |
| `PCR_PROFILE_GET_DUPLICATE_NAME_RESPONSE id: %s newname: %s` | Device -> App | Duplicate name response |
| `Sending saved programs available` | Device -> App | Profile list response |
| `Sending device info` | Device -> App | Device info response |

### PCR Run Control

| Command/Function | Direction | Description |
|-----------------|-----------|-------------|
| `sendPcrProfileToRun` | App -> Device | Start PCR run with loaded profile |
| `stopPcrProgram()` | App -> Device | Stop running PCR program |
| `startPcrProgram()` | Internal | Firmware starts PCR (Heat::startPcrProgram()) |
| `sendStagesToDevice` | App -> Device | Send individual stages |

### Device Control

| Command/Function | Direction | Description |
|-----------------|-----------|-------------|
| `updateCentMode` | App -> Device | Control centrifuge mode |
| `updateGelMode` | App -> Device | Control gel electrophoresis / transilluminator |
| `setBlockTargetTemperature` | App -> Device | Set heat block target temp |
| `checkIfUpdateAvailable` | App -> Device | Check for firmware updates |

## PCR Profile Data Model

### Wire Format (fromSerial)

Profile data is serialized as text and parsed by `PCR program fromSerial: %s`.

```
Profile fields:
  profileId     — integer ID
  name          — string, parsed by "getting name in fromSerial: %s"
  lidTemperature — temperature in format %s%d.%02d (sign + int.frac)

Cycle:
  from, to      — stage index range
  numCycles     — repeat count

Stage:
  temperature   — format: %s%d.%02d (sign, degrees, centidegrees)
  duration      — integer (seconds)
  touchDownDelta    — optional: %s%d.%02d
  touchDownRepeats  — optional: integer
```

### Data Model Hierarchy (from Dart classes)

```
PcrProgram (JSON serializable)
  ├── name: String
  ├── profileId: int
  ├── lidTemperature: double
  ├── bentoPcrProfileVersion: int
  ├── PcrProgramCycle[]
  │     ├── from: int (start stage index)
  │     ├── to: int (end stage index)
  │     ├── repeatCount: int
  │     └── touchdownRepeats: int (optional)
  └── PcrProgramStage[]
        ├── temperature: double (Celsius)
        ├── duration: int (seconds)
        └── touchdownDelta: double (optional, for touchdown PCR)
```

### Firmware Limits

```c
PCR_MAX_CYCLENODES  // Maximum number of cycles per profile
PCR_MAX_STAGENODES  // Maximum number of stages per profile
```

## Temperature Encoding

Temperatures are encoded as signed fixed-point: `%s%d.%02d`
- `%s` = sign ("" or "-")
- `%d` = integer degrees
- `.%02d` = centidegrees (hundredths)
- Example: `95.00` = 95.00°C, `-4.50` = -4.50°C

Firmware uses PID control with logging:
```
PERFORMANCE: target: %.2f, Overshoot: %.2f, Slowdown time: %lu ms,
             Hold variance: %.4f, lidTemp: %.2f, lidVoltage: %d
```

## Lid Temperature Control States

```
LID_MODE_HEATUP -> LID_MODE_MAINTAIN_APPROACHING -> LID_MODE_MAINTAIN
```

Error: `LID_TEMP_ERROR_HEATING` if temperature too low after 20 seconds.

## Power Management

- 100W USB-C PD: `POWER 100W - FULL HEATER SPEED!`
- 60W USB-C PD: `POWER 60W - HEATER SPEED WILL BE LIMITED!`
- Power detection affects heater performance

## Firmware Update (DFU)

- MCU: **nRF52840** (ARM Cortex-M4F)
- DFU: Nordic Semiconductor Secure DFU
- Firmware served from: `https://api2.bento.bio/static/firmware-images/`
- Known packages: `bg-p000-1.zip` (confirmed downloadable, 124KB)
- Package format: ZIP containing `nrf52840_xxaa.bin` + `nrf52840_xxaa.dat` + `manifest.json`
- DFU trigger: `Power management wants to reset to DFU mode`

## Error Codes (from firmware + APK)

| Error | Message |
|-------|---------|
| Centrifuge lid | Centrifuge lid state is uncertain |
| Centrifuge lock | Centrifuge lock can not be confirmed |
| Centrifuge stuck | Centrifuge may be stuck and can not be unlocked |
| Centrifuge motor | Centrifuge motor is not responding |
| Heat block sensor | Heat block temperature sensor error |
| Heat block range | Heated block temperature out of range |
| Lid sensor | Lid temperature sensor error |
| Lid range | Heated lid temperature out of range |
| Gel current high | Gel current is too high at this voltage |
| Gel no current | Gel is not drawing current (wire disconnected?) |
| USB power | Cannot use current USB-C power supply |

## Device State Machine

```
IDLE
  ├── sendPcrProfileToRun -> HEATING (lid heats first)
  │     ├── LID_MODE_HEATUP -> LID_MODE_MAINTAIN_APPROACHING -> LID_MODE_MAINTAIN
  │     └── Heat::startPcrProgram() -> RUNNING
  │           ├── Cycle through stages (temperature + duration)
  │           ├── Fast cooling with rate monitoring
  │           │     "Fast cooling stop: rateOfChange=%.2f, remainingDifference=%.2f"
  │           │     "Moving very fast and almost at target, stop cooling immediately"
  │           ├── stopPcrProgram() -> IDLE
  │           └── Complete all cycles -> COMPLETE -> IDLE
  ├── updateCentMode -> CENTRIFUGE_RUNNING
  └── updateGelMode -> GEL_RUNNING (transilluminator on)
```

## Next Steps

- [ ] Connect to device, subscribe to NUS TX, and capture actual command bytes
- [ ] Send `PCR_LIST_NUM` and observe response format
- [ ] Send `PCR_REQUEST_PROFILE 0` to get a stored profile
- [ ] Capture a full PCR profile upload sequence via BLE monitor
- [ ] Determine exact delimiters (newline? null byte? length prefix?)
- [ ] Disassemble `nrf52840_xxaa.bin` with Ghidra/IDA for complete command table
