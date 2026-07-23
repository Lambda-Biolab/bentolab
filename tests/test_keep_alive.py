"""Keep-alive task tests for BentoLabBLE.

We don't go through `connect` (that needs bleak machinery); instead we
exercise `_start_keep_alive` and `_keep_alive_loop` directly with a
patched `_send`, the way the real connect path would.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from bentolab.ble_client import BentoLabBLE, BentoLabConnectionError


class _FakeClient:
    is_connected = True


@pytest.fixture
def lab(monkeypatch: pytest.MonkeyPatch) -> BentoLabBLE:
    inst = BentoLabBLE(keep_alive_seconds=0.05)
    inst._client = _FakeClient()  # type: ignore[assignment]

    sent: list[str] = []

    async def fake_send(cmd: str) -> None:
        sent.append(cmd)

    monkeypatch.setattr(inst, "_send", fake_send)
    inst._sent = sent  # type: ignore[attr-defined]
    return inst


async def test_keep_alive_emits_xa_periodically(lab: BentoLabBLE) -> None:
    lab._start_keep_alive()
    try:
        await asyncio.sleep(0.18)  # enough for ~3 ticks at 50 ms cadence
    finally:
        if lab._keep_alive_task:
            lab._keep_alive_task.cancel()
    sent: list[str] = lab._sent  # type: ignore[attr-defined]
    assert sent.count("Xa") >= 2
    assert all(c == "Xa" for c in sent)


async def test_keep_alive_stops_when_disconnected(lab: BentoLabBLE) -> None:
    lab._start_keep_alive()
    await asyncio.sleep(0.06)
    lab._client = None  # simulate disconnect
    await asyncio.sleep(0.1)
    # Task should have noticed and exited cleanly.
    assert lab._keep_alive_task is not None
    assert lab._keep_alive_task.done()


async def test_keep_alive_swallows_send_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    inst = BentoLabBLE(keep_alive_seconds=0.05)
    inst._client = _FakeClient()  # type: ignore[assignment]

    async def boom(_cmd: str) -> None:
        raise BentoLabConnectionError("link gone")

    monkeypatch.setattr(inst, "_send", boom)
    inst._start_keep_alive()
    await asyncio.sleep(0.1)
    assert inst._keep_alive_task is not None
    assert inst._keep_alive_task.done()


def test_zero_disables_keep_alive() -> None:
    inst = BentoLabBLE(keep_alive_seconds=0.0)
    inst._start_keep_alive()
    assert inst._keep_alive_task is None


# Suppress unused-import lint
_ = Any
