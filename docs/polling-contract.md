# Computer Consumer Polling Contract

How a long-running computer process polls the Bento Lab for status and
activity. Covers the three transports we ship (SSE, HTTP polling,
long-polling) and the self-healing behavior the SSE stream implements
for the firmware's hard ~90s connection lifetime (issue #55).

## TL;DR

| Need | Use | Latency |
|------|-----|---------|
| Real-time telemetry (preferred) | `GET /events` (SSE) | sub-second |
| Status snapshot at any time | `GET /status` | request/response |
| State-change events | `GET /runs/{id}?wait=N` (long-poll) | up to N seconds |
| Run lifecycle | `POST /runs` / `POST /runs/{id}/abort` / `GET /runs/{id}/results` | request/response |

If the BLE link drops (the Bento firmware disconnects every ~95s —
see [issue #55](https://github.com/Lambda-Biolab/bentolab/issues/55)),
the SSE stream self-heals; the other transports serve a degraded
state until the link is back.

## SSE: real-time telemetry with self-heal

```text
GET /events HTTP/1.1
Accept: text/event-stream
```text

The server emits Server-Sent Events for as long as the client stays
connected. Five event families:

| `event:` field | Meaning |
|----------------|---------|
| `connected` | First event after a fresh connection. `data: {"device": "<BLE address>"}` |
| `status` | Device status broadcast (~5s interval). `data: {"running": bool, "block": C, "lid": C}` |
| `run` | Polled run status (~5s interval). `data: {"running": bool, "progress": 0-100}` |
| `disconnected` | BLE link dropped. `data: {"retry_after_ms": 1000}`. Followed by `retry: 1000` |
| `reconnected` | Server's background reconnect succeeded. `data: {"device": "<BLE address>"}` |

Plus `: keep-alive` comment lines every 15s so reverse proxies don't
idle the connection.

### Self-heal flow

```text
t=0     : Client opens /events
t=0     : Server emits event: connected
t=0     : Server emits event: status (initial snapshot)
t=5     : event: status
t=10    : event: run
t=15    : event: status
... (telemetry continues)
t=95    : BLE link drops (Bento firmware 95s lifetime, issue #55)
t=95+ε  : Server emits event: disconnected + retry: 1000
t=95+ε  : Server's _on_disconnect schedules a background
          _background_reconnect task (5s/10s/20s/30s backoff)
t=95+ε  : Server closes the SSE response
t=96    : Client sees the close, follows retry hint, reconnects
t=96    : New SSE connection: event: connected
t=96    : NO fresh data yet -- server's BLE is still down
... (telemetry gap; client may see only keep-alive comments)
t=100+ε : Server's _background_reconnect succeeds
t=100+ε : Server publishes event: reconnected to the broker
t=100+ε : Live SSE consumers see event: reconnected
t=101   : event: status (fresh)
... (telemetry resumes)
```text

The gap between t=96 and t=100+ε is the price the client pays for
the firmware's hard link lifetime. Operators see a brief telemetry
pause, not a permanent stream break. The actual run on the device is
unaffected — the device never aborts a run because the host is
disconnected.

### Reconnect strategy on the client

A standards-compliant SSE consumer should:

1. Honor the `retry:` field — sleep that long before reconnecting.
2. On `event: disconnected`, immediately reconnect (don't wait the
   full retry interval if the disconnect was unexpected).
3. Track the `event: reconnected` event to know when live data
   resumes; data seen before that may be stale or from the previous
   device session.
4. Cap total reconnects if you want a hard limit, but the server's
   self-heal is reliable, so an unbounded loop is fine for
   long-running consumers.

A reference implementation in Python is at
[`examples/elabftw_demo.py`](../examples/elabftw_demo.py) (uses
`lab-copilot-gateway`'s `HttpBentoLabClient`).

## HTTP polling: status snapshot at any time

```text
GET /status
```text

```json
{
  "state": "disconnected" | "idle" | "running" | "aborted" | "complete" | "error",
  "block_temperature": 27,
  "lid_temperature": 28
}
```text

Always returns 200, even when the BLE link is down (state will be
`disconnected`). Use this for one-shot health checks or for
non-streaming clients that can't hold a long-lived SSE connection.

## Long-polling: state-change events

```text
GET /runs/{id}?wait=30
```text

Blocks for up to 30 seconds waiting for the run's state to change.
Returns the current `RunStatusDetail` as soon as it does. Returns the
current state immediately if the run is already terminal. Used by the
elabFTW gateway to wait for run completion without burning CPU on a
poll loop.

## Run lifecycle

```text
POST /runs                  # Start a new run
POST /runs/{id}/abort       # Abort a running run
GET /runs/{id}/results      # Final temperature log + lifecycle
GET /runs                   # List all known runs
```text

All return `application/json`. See [`docs/cli-scripting.md`](cli-scripting.md)
for the C22 contract these implement.

## Why this works despite the 95s drop

The Bento Lab firmware has a hard ~90s connection lifetime that no
host-side keep-alive can extend (verified across 3 soak runs with
different strategies, all drop at t≈95.4s — see issue #55). The
mitigation is layered:

1. **SSE consumer** reconnects after the `retry: 1000` hint.
2. **Server** runs `_background_reconnect` with exponential backoff to
   pull the BLE link back up.
3. **`event: reconnected`** tells the consumer when live data is back.

This means a long-running computer consumer never has to know the
firmware has a bug — it just sees brief gaps and the gap-filling
happens transparently on both ends.

## Known limits

- The 5s-30s reconnect backoff means the BLE link can be down for up
  to ~35s after a drop (worst case: 5s sleep + 20s connect timeout +
  5s sleep + retry). Operators see this as a telemetry gap.
- The reconnect task gives up after 10 attempts (~5 min of backoff).
  If the device is genuinely unreachable, an operator needs to
  restart the server.
- The auto-reconnect only targets the last-connected address. If you
  pair a different device, you need to call `POST /devices/reconnect`
  or restart the server.
