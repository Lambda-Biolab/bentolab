# Firmware Analysis

**Status:** INITIAL ANALYSIS COMPLETE
**Source:** `bg-p000-1.zip` from `https://api2.bento.bio/static/firmware-images/`
**Date:** 2026-04-10

## Binary Format

Nordic DFU package (ZIP) containing:
- `nrf52840_xxaa.bin` — Application firmware (123KB)
- `nrf52840_xxaa.dat` — DFU init packet (142 bytes, contains signature + metadata)
- `manifest.json` — DFU manifest

## MCU Identification

| Property | Value |
|----------|-------|
| Architecture | ARM Cortex-M4F |
| MCU | Nordic nRF52840 |
| Flash | 1MB |
| RAM | 256KB |
| Compiler | GCC (C++ with RTTI: `__cxxabiv117__class_type_info`) |
| BLE Stack | Nordic SoftDevice (sd_ble_*) |
| Logging | SEGGER RTT (`rtt_log_backend`) |
| RTOS | None (bare-metal with SoftDevice) |

## Memory Layout

Vector table at 0x00000000 (standard Cortex-M):
```
0x00000000: Initial SP  = 0x20040000 (top of 256KB RAM)
0x00000004: Reset vector = 0x000272BD
```

## Hardware Peripherals (from firmware strings)

| Peripheral | Purpose | Notes |
|------------|---------|-------|
| SPI (`nrfx_spi_init`) | Temperature sensor ADC? | TWI/I2C also present |
| TWI/I2C (`TWI error`) | Sensor communication | `NRF_ERROR_DRV_TWI_ERR_ANACK/DNACK` |
| GPIOTE | Button/GPIO interrupts | `app_button` module |
| UARTE | Debug UART | `Debug logging for UART over RTT started` |
| Timer | PCR timing, sleep | `Go to sleep timer started` |
| Flash storage | PCR profile storage | `nrf_fstorage`, `pcrstore_save` |
| Peer Manager | BLE bonding | Full Nordic PM stack |

## Power Management

- USB-C Power Delivery detection (60W vs 100W)
- Heater block control with voltage regulation
- Sleep mode with wake-on-button
- DFU bootloader entry via power management

## Key Firmware Functions

| Function | Purpose |
|----------|---------|
| `handle_command()` | Main command dispatcher (state machine) |
| `fromSerial()` | Parse PCR program from serial/BLE text |
| `blueserial_writechar()` | Send data over BLE NUS |
| `Heat::startPcrProgram()` | Start PCR thermal cycling |
| `stopPcrProgram()` | Stop PCR run |
| `setBlockTargetTemperature()` | Set heat block target |
| `refreshNumberedProfilesAvailable()` | Refresh stored profile list |
| `pcrstore_save()` | Save PCR profile to flash |

## Interesting Strings

```
Bento Bioworks Ltd              — Manufacturer name (BLE Device Info)
Bento goPCR                     — Device name base
Bento goPCR %02X%02X            — Device name with MAC suffix
bento-pcr                       — Internal identifier
App Version 0.5.4               — Compiled with app version reference
sizeof(pcr_profile_t)=%d        — Profile struct size logged at runtime
unchunked_extended_messages: %d  — BLE message chunking support
```

## Notes

- This firmware appears to be for the **goPCR** device variant (`bg-p000-1.zip`,
  `Bento goPCR` device name), not the Bento Lab main unit
- The Bento Lab main unit firmware (`dfu-1.15.zip`, `dfu-2.2.zip`) returned 404 —
  may have been rotated or versioned differently
- The protocol and command structure should be identical between goPCR and Bento Lab
  (shared codebase: `BentoPcrDevice` is a common base class)
- Full disassembly with Ghidra would reveal the complete command table
