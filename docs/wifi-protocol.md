# Wi-Fi Protocol — Bento Lab Pro V1.31

**Status:** INVESTIGATION COMPLETE — No remote control interface exists
**Device:** BL13125 (Pro V1.31, Wi-Fi, firmware v1.3.17.4)
**Date:** 2026-04-10

## Conclusion

The V1.31's Wi-Fi connectivity is used **exclusively for firmware updates**.
There is no remote control protocol. The device is controlled only via its
physical e-ink display and click-dial interface. Remote control was added
in the V1.4 hardware revision (nRF52840 + BLE).

## Device Discovery

| Method | Result |
|--------|--------|
| IP Address | 192.168.188.3 (DHCP) |
| MAC Address | c4:3c:b0:0e:98:76 |
| MAC OUI Vendor | Shenzhen Bilian Electronic (ESP32 module) |
| mDNS Hostname | None advertised |
| mDNS Services | None advertised |
| BLE | Not present (no Bluetooth hardware) |

## Open Ports (full 65535 TCP scan)

| Port | Protocol | Service | Purpose |
|------|----------|---------|---------|
| 53 | TCP | DNS | Captive portal / WiFi setup |
| 9000 | TCP | Custom | OTA firmware upload receiver |

No other ports open across the entire TCP range (1-65535).

## Port 9000 Analysis

The OTA receiver on port 9000:
- Accepts TCP connections silently
- Does **not** respond to any commands (text or binary)
- Does **not** send status broadcasts
- Silently consumes incoming data (firmware binary upload)
- Resets connection on certain invalid byte patterns (e.g., 0xE0 prefix)
- Does **not** speak HTTP, WebSocket, or any text protocol
- Does **not** respond to the BLE NUS protocol commands (`_.;Xa\n\n`, etc.)

### What was tested on port 9000

| Probe | Result |
|-------|--------|
| BLE protocol commands (`_.;Xa\n\n`, `_.;p\n\n`) | Silently consumed, no response |
| Single-char commands (`?`, `h`, `v`, `i`, `s`) | Silently consumed |
| JSON commands (`{"cmd":"status"}`) | Silently consumed |
| HTTP GET/POST | Connection reset |
| WebSocket upgrade | Connection reset |
| Binary probes (ESP OTA magic, length-prefix) | Silently consumed |
| ESP-IDF OTA begin (0xE0 + zeros) | Connection reset (parsed and rejected) |
| Bare commands without `_.;` prefix | Silently consumed |
| CR, LF, CRLF, null terminators | Silently consumed |
| Listening for unprompted data (15s) | Nothing received |

## OTA Update Flow

The device downloads firmware **directly from the internet**, not through the app:

1. Device checks for updates from `https://api2.bento.bio/` (exact endpoint unknown)
2. Firmware is downloaded directly by the ESP32 over HTTPS
3. API returns metadata: `new_version_checksum`, `new_version_filename`, `new_version_str`
4. Download URL is under `https://api2.bento.bio/static/firmware-images/` (directory listing forbidden)
5. The legacy update path `bl-legacy-update/` was found in the app but returns 404

### Firmware URL enumeration

Over 200 filename patterns were tested against the firmware server:
- `bl-*.zip/bin`, `bl-p00X-X.zip`, `bl-1.3.17.4.*`
- Version-based, hardware-based, and generic patterns
- All returned 404 — the exact filename is unknown

The goPCR firmware (`bg-p000-1.zip`) was the only downloadable file found.

## App Support

- The current Bento Bio app (v0.5.4, Flutter) uses **BLE only** (`flutter_blue_plus`)
- The app has a `ScreenBLLegacyUpdater` and `device_bl.dart` for legacy devices
- No older WiFi-compatible app version exists publicly (checked APKPure, APKMirror, Google Play)
- WiFi was always for firmware updates, never for device control

## Hardware-Level Options (not attempted)

If remote control of the V1.31 is needed in the future:

1. **UART/flash dump**: Open the case, connect to ESP32 UART pins, use `esptool.py read_flash`
   to dump the firmware, reverse-engineer and patch to add a TCP command listener
2. **BLE bridge**: Use a Raspberry Pi Pico W or ESP32 connected to the V1.31's internal
   UART bus, bridging serial commands to WiFi/BLE
3. **Custom firmware**: Write a replacement firmware for the ESP32 that exposes the same
   `handle_command()` protocol over TCP
