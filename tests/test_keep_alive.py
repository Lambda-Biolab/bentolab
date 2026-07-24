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

    async def fake_send_with_response(cmd: str) -> None:
        sent.append(cmd)

    monkeypatch.setattr(inst, "_send", fake_send)
    monkeypatch.setattr(inst, "_send_with_gatt_response", fake_send_with_response)
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

    monkeypatch.setattr(inst, "_send_with_gatt_response", boom)
    inst._start_keep_alive()
    await asyncio.sleep(0.1)
    assert inst._keep_alive_task is not None
    assert inst._keep_alive_task.done()


def test_zero_disables_keep_alive() -> None:
    inst = BentoLabBLE(keep_alive_seconds=0.0)
    inst._start_keep_alive()
    assert inst._keep_alive_task is None


def test_default_keep_alive_cadence_is_fifteen_seconds() -> None:
    """Regression: the default cadence dropped from 30s to 15s.

    The Bento Lab firmware drops the BLE link after roughly 3 keep-alive
    intervals (~90s at 30s cadence) regardless of whether Xa is acked.
    15s puts us at ~45s link supervision which is well within the
    device's link budget. See tools/keep_alive_soak.py for the soak
    that discovered this.
    """
    inst = BentoLabBLE()
    assert inst.keep_alive_seconds == 15.0


# Suppress unused-import lint
_ = Any


# ---------------------------------------------------------------------------
# Hardware regression: 5-minute soak against a real Bento Lab
# ---------------------------------------------------------------------------


@pytest.mark.hardware
@pytest.mark.slow
@pytest.mark.xfail(
    reason="Link still drops at ~95s (see issue #55) -- the 15s cadence "
    "addresses a separate timeout but the device firmware drops the "
    "link at a deterministic ~90s regardless of keep-alive activity. "
    "Remove this xfail when #55 is fixed.",
    strict=False,
)
def test_keep_alive_holds_for_5_minutes() -> None:
    """Hardware soak: link must stay up for 5 min with the new 15s cadence.

    Runs ``tools/keep_alive_soak.py`` as a subprocess. Exits 0 if the
    link was held for the full 5 min with no disconnects, 1 otherwise.
    Marked ``@pytest.mark.hardware`` so it's excluded from CI; run
    manually with ``pytest -m hardware`` when a device is in range.

    Currently xfail: the keep-alive did not solve the drop (issue #55).

    To re-run without pytest::

        uv run python tools/keep_alive_soak.py \\
            --device <addr> --duration 300
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    device = os.environ.get("BENTOLAB_SOAK_DEVICE", "")
    if not device:
        pytest.skip("BENTOLAB_SOAK_DEVICE not set; skipping hardware soak")

    # Use a 2-minute soak by default to keep CI-like invocations short;
    # the full 5-minute version is for manual / nightly runs.
    duration = int(os.environ.get("BENTOLAB_SOAK_DURATION", "120"))

    proc = subprocess.run(
        [
            sys.executable,
            "tools/keep_alive_soak.py",
            "--device",
            device,
            "--duration",
            str(duration),
            "--poll-interval",
            "10",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=duration + 60,
    )
    # Print the soak output on failure for debuggability
    if proc.returncode != 0:
        print("--- soak stdout ---")
        print(proc.stdout)
        print("--- soak stderr ---")
        print(proc.stderr)
    assert proc.returncode == 0, f"soak reported failure: {proc.returncode}"
