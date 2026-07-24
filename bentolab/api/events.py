"""Server-Sent Events (SSE) telemetry stream for the BentoLab HTTP API.

Exposes a one-way push stream of status broadcasts and run-status polls
so a connected client (browser, LLM tool, ops dashboard) can see live
device state without polling. The transport is plain HTTP/1.1 chunked
text -- no WebSocket dependency, no extra libraries.

Wire format (``text/event-stream``)
-----------------------------------
One event per line of meaningful activity. The stream stays open
indefinitely; clients reconnect on EOF with the ``Last-Event-ID``
header. The example below shows two events separated by a blank
line (the SSE record terminator)::

    event: status
    data: {"running": false, "block": 25.0, "lid": 24.0}

    event: run
    data: {"running": true, "progress": 42}

Event kinds
-----------
``status`` -- emitted whenever the device pushes a status broadcast
(block + lid temperatures, running flag).

``run`` -- emitted whenever the periodic run-status poll returns a
new value. The poll interval is configurable (default 5 s).

``connected`` -- emitted once on stream open so the client knows the
server is alive. Carries the current device address (or empty).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .app import BleClientProtocol

logger = logging.getLogger(__name__)

# Per-subscriber queue depth. A slow consumer (network stall) sees
# events dropped rather than back-pressure the broker; a fast consumer
# sees no drops in practice (status updates are << 1 Hz).
_DEFAULT_QUEUE_MAX = 100

# How often to poll the device for run status when no status broadcast
# has changed. Matches the C22 contract note about "active polls every
# 5 s when subscribed".
_DEFAULT_POLL_INTERVAL_S = 5.0

# Upper bound on how long the stream may sit with no events before the
# server sends a comment line (``:``) to keep the connection warm. Many
# proxies close idle HTTP connections after 30-60 s.
_KEEPALIVE_INTERVAL_S = 15.0


@dataclass(frozen=True)
class TelemetryEvent:
    """A single event on the SSE stream."""

    kind: str
    data: dict[str, Any]
    event_id: str | None = None


class EventBroker:
    """Fan-out broker for telemetry events.

    Each subscriber gets its own bounded :class:`asyncio.Queue`. When
    :meth:`publish` is called, the event is enqueued on every current
    subscriber. If a subscriber's queue is full, the event is dropped
    for that subscriber only -- the broker and other subscribers are
    unaffected.
    """

    def __init__(self, max_queue: int = _DEFAULT_QUEUE_MAX) -> None:
        self._subscribers: set[asyncio.Queue[TelemetryEvent]] = set()
        self._max_queue = max_queue

    def subscribe(self) -> asyncio.Queue[TelemetryEvent]:
        q: asyncio.Queue[TelemetryEvent] = asyncio.Queue(maxsize=self._max_queue)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[TelemetryEvent]) -> None:
        self._subscribers.discard(q)

    def publish(self, event: TelemetryEvent) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer -- drop the event for this subscriber.
                # Better than blocking the publisher, which would
                # corrupt the order of events for other subscribers.
                logger.debug("SSE subscriber queue full; dropping event %s", event.kind)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


def _sse(
    kind: str,
    data: dict[str, Any],
    *,
    event_id: str | None = None,
) -> str:
    """Format one SSE record as a multi-line string (with trailing blank line)."""
    parts: list[str] = []
    if event_id:
        parts.append(f"id: {event_id}")
    parts.append(f"event: {kind}")
    parts.append(f"data: {json.dumps(data, default=str)}")
    parts.append("")  # required blank line terminator between records
    parts.append("")
    return "\n".join(parts)


def _sse_comment(text: str) -> str:
    """Format an SSE comment line (``:`` prefix). Used as keep-alive."""
    return f": {text}\n\n"


def _status_to_dict(status: Any) -> dict[str, Any]:
    return {
        "running": bool(getattr(status, "running", False)),
        "block": getattr(status, "block_temperature", None),
        "lid": getattr(status, "lid_temperature", None),
    }


def _run_to_dict(run: Any) -> dict[str, Any]:
    return {
        "running": bool(getattr(run, "running", False)),
        "progress": int(getattr(run, "progress", 0)),
    }


async def stream_events(
    broker: EventBroker,
    ble: BleClientProtocol | None,
    *,
    poll_interval: float = _DEFAULT_POLL_INTERVAL_S,
    keepalive_interval: float = _KEEPALIVE_INTERVAL_S,
) -> AsyncIterator[str]:
    """Yield SSE-formatted text for as long as the client stays connected.

    Thin wrapper that sets up broker subscription and BLE status
    callback, yields initial events, runs the dispatch loop, and
    cleans up on disconnect. See :func:`_initial_events` and
    :func:`_dispatch_loop` for the per-stage details.
    """
    queue = broker.subscribe()
    status_cb: Any = None
    if ble is not None:
        status_cb = lambda s: broker.publish(  # noqa: E731
            TelemetryEvent(kind="status", data=_status_to_dict(s))
        )
        ble.on_status(status_cb)

    try:
        async for chunk in _initial_events(ble):
            yield chunk
        async for chunk in _dispatch_loop(broker, ble, queue, poll_interval, keepalive_interval):
            yield chunk
    finally:
        if ble is not None and status_cb is not None:
            with contextlib.suppress(Exception):
                ble.off_status(status_cb)
        broker.unsubscribe(queue)


async def _initial_events(ble: Any) -> AsyncIterator[str]:
    """Emit the opening ``connected`` event and an optional status snapshot."""
    device_address: str = ""
    if ble is not None:
        try:
            device_address = str(getattr(ble, "_connected_address", "") or "")
        except Exception:
            device_address = ""

    yield _sse("connected", {"device": device_address}, event_id="connected")

    if ble is not None and ble.is_connected:
        try:
            status = await ble.get_status()
            yield _sse("status", _status_to_dict(status), event_id="initial-status")
        except Exception:
            logger.debug("Initial status snapshot failed", exc_info=True)


async def _dispatch_loop(
    broker: EventBroker,
    ble: Any,
    queue: asyncio.Queue[TelemetryEvent],
    poll_interval: float,
    keepalive_interval: float,
) -> AsyncIterator[str]:
    """Yield events, run periodic run-status polls, and emit keep-alives."""
    loop = asyncio.get_event_loop()
    last_poll = loop.time()
    last_keepalive = loop.time()

    while True:
        # Wait briefly for the next broker event.
        try:
            event = await asyncio.wait_for(queue.get(), timeout=0.5)
        except TimeoutError:
            event = None
        if event is not None:
            yield _sse(event.kind, event.data, event_id=event.event_id)
            last_keepalive = loop.time()

        now = loop.time()
        if ble is not None and ble.is_connected and (now - last_poll) >= poll_interval:
            last_poll = now
            with contextlib.suppress(Exception):
                run = await ble.get_run_status()
                broker.publish(TelemetryEvent(kind="run", data=_run_to_dict(run)))

        if (now - last_keepalive) >= keepalive_interval:
            last_keepalive = now
            yield _sse_comment("keep-alive")


__all__ = [
    "EventBroker",
    "TelemetryEvent",
    "stream_events",
]
