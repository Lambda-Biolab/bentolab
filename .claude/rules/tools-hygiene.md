# Tools Hygiene

- Debug scripts go in `tools/` (scanner, commander, monitor)
- All BLE interactions MUST use async context managers for connection cleanup
- Never commit raw HCI snoop captures — reference by path only
- Test with mock BLE (pytest-asyncio), not real hardware, in default test suite
