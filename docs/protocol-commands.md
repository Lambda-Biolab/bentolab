# Protocol Command Reference

**Status:** NOT STARTED

## Command Format

_Packet structure: header, opcode, length, payload, checksum._

## Known Commands

| Opcode | Name | Payload Format | Response | Description |
|--------|------|---------------|----------|-------------|
| | | | | |

## Response Format

_How the device responds to commands._

## State Machine

_Device states and valid transitions._

```
IDLE -> HEATING -> RUNNING -> COMPLETE
                -> PAUSED -> RUNNING
                          -> IDLE
```
