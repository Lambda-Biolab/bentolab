"""``bentolab run`` — upload a profile and execute it."""

from __future__ import annotations

import asyncio

import typer

from .. import profiles as profile_store
from .._logging import SessionLogger
from ..ble_client import BentoLabBLE
from ._device import resolve_address
from ._format import emit_json, fail, stdout


def run_command(
    name: str = typer.Argument(..., help="Profile name (from `bentolab profile list`)."),
    device: str | None = typer.Option(None, "--device", help="BLE address."),
    lid: float | None = typer.Option(None, "--lid", help="Override lid temperature (°C)."),
    no_tail: bool = typer.Option(False, "--no-tail", help="Start the run and exit."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON-per-line state."),
) -> None:
    """Upload the named profile, start the run, and tail until completion."""
    try:
        profile = profile_store.load(name)
    except profile_store.ProfileNotFoundError:
        fail(f"profile not found: {name}", code=2)

    lid_temp = lid if lid is not None else profile.lid_temperature
    address = resolve_address(device)

    try:
        asyncio.run(_run(profile, lid_temp, address, no_tail, json_output))
    except Exception as e:
        fail(f"run failed: {e}")


async def _run(
    profile, lid_temp: float, address: str | None, no_tail: bool, json_output: bool
) -> None:
    lab = BentoLabBLE(address=address) if address else BentoLabBLE()
    with SessionLogger(profile.name) as log:
        log.event("run_config", {"profile": profile.to_dict(), "lid_temp": lid_temp})
        async with lab:
            log.event("connected", {"address": getattr(lab, "_connected_address", None)})
            if no_tail:
                stages, cycles = profile.to_stages_and_cycles()
                await lab.start_run(
                    name=profile.name, stages=stages, cycles=cycles, lid_temp=lid_temp
                )
                log.event("run_started", {"profile": profile.name, "tail": False})
                stdout.print(f"[green]Started:[/green] {profile.name}")
                return

            log.event("run_started", {"profile": profile.name, "tail": True})
            async for state in lab.run_profile(profile, lid_temp=lid_temp):
                payload = {
                    "running": state.running,
                    "progress": state.progress,
                    "block": state.block_temperature,
                    "lid": state.lid_temperature,
                    "elapsed": state.elapsed_seconds,
                }
                log.event("run_progress", payload)
                if json_output:
                    emit_json(payload)
                else:
                    stdout.print(
                        f"running={state.running} progress={state.progress:>3}% "
                        f"block={state.block_temperature:.1f}°C "
                        f"lid={state.lid_temperature:.1f}°C "
                        f"elapsed={state.elapsed_seconds:.0f}s"
                    )
            log.event("run_finished", {"profile": profile.name})
