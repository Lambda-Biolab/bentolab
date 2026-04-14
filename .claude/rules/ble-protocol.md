# BLE Protocol Constraints

## Transport
Nordic UART Service (NUS) over BLE, nRF52840 MCU.

## Framing
- Commands: `_.;<payload>\n\n`
- Responses: semicolon-delimited
- Status broadcast: device sends `bb;...` every ~5 seconds when connected

## Profile upload sequence
Must follow exact order: `pb` -> `w` -> stages (`x`) -> cycles (`z`) ->
lid temp (`A`) -> name (`I`) -> slot (`B`) -> finalize (`B`)

## macOS caveat
CoreBluetooth exposes UUIDs only, not GATT handle numbers. All code must
use UUID-based lookups, never raw handles.

## Key UUIDs
- `6e400001-...` — NUS Service
- `6e400002-...` — NUS RX (write commands to device)
- `6e400003-...` — NUS TX (notifications from device)
- `6e409a18-...` — Bento advertising / scan filter
