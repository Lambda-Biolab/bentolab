# Agent Learnings

Patterns and gotchas discovered while working on this codebase.

## BLE Response Chunking

**Context**: BLE notifications have a maximum payload size (~20 bytes for
default MTU). Longer responses from the Bento Lab are split across multiple
NUS TX notifications.

**Problem**: Parsing a single notification as a complete message can fail when
the device splits a response (e.g., touchdown stage `y;3;68.00;20;-1.00;8`
arrives in two chunks).

**Solution**: The `_collect_responses` method in `ble_client.py` accumulates
responses over a timeout window. Continuation messages (`;;;`) are filtered
out. Always use `_collect_responses` rather than reading a single notification.

## macOS CoreBluetooth UUID-Only Addressing

**Context**: On macOS, CoreBluetooth does not expose raw GATT handle numbers.
Devices are addressed by UUID only.

**Problem**: Code that references GATT handles directly (common in Linux
BlueZ examples) will not work on macOS.

**Solution**: Always use UUID strings (e.g., `NUS_RX_CHAR_UUID`) for
characteristic access via bleak. Never hardcode handle integers.

## Protocol Command Prefix

**Context**: All commands to the Bento Lab must be wrapped in `_.;<cmd>\n\n`
framing.

**Problem**: Sending raw command strings without the prefix results in the
device silently ignoring the message.

**Solution**: Always use `encode_command()` or the typed `encode_*` helpers
from `protocol.py`. Never construct raw command bytes manually.

## Wi-Fi Client is a Stub

**Context**: The V1.31 Wi-Fi unit's protocol has not been reverse-engineered.

**Problem**: `wifi_client.py` methods raise `NotImplementedError`.

**Solution**: Do not write tests or integrations against `BentoLabWiFi`
beyond construction/connection. Protocol work is blocked on capture analysis.

## Status Broadcast Timing

**Context**: The Bento Lab sends `bb;...` status broadcasts every ~5 seconds
when a BLE connection is active.

**Problem**: `get_status()` may block up to 10 seconds waiting for the first
broadcast if called immediately after connection.

**Solution**: The handshake (`Xa`) triggers an early status. A 0.5s sleep
after handshake in `connect()` gives the device time to respond before
the first `get_status()` call.
