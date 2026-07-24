"""``bentolab serve`` — start the BentoLab HTTP API server.

Wraps :func:`uvicorn.run` with the project's standard ``create_app``
factory. The server can be started with no hardware (open mode) for
local development, or pointed at a real BLE device for end-to-end
testing and demos.
"""

from __future__ import annotations

import typer
import uvicorn

from ..api.app import create_app
from ._format import fail

serve_app = typer.Typer(help="Start the BentoLab HTTP API server.")


@serve_app.command("start")
def serve_start(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address."),
    port: int = typer.Option(8765, "--port", help="Bind port."),
    require_auth: bool = typer.Option(
        False,
        "--require-auth",
        help=(
            "Force bearer-token auth even when no tokens are registered. "
            "Recommended for any non-localhost deployment."
        ),
    ),
    no_hw: bool = typer.Option(
        False,
        "--no-hw",
        help=(
            "Start the server in degraded mode (no BLE client). Useful for "
            "local development when no Bento Lab is in range. Default is "
            "to construct a real BentoLabBLE."
        ),
    ),
    reload: bool = typer.Option(False, "--reload", help="Enable autoreload (dev only)."),
) -> None:
    """Run the BentoLab HTTP API server in the foreground.

    The server speaks the C22 contract (see ``docs/``) plus the elabFTW
    extensions added for #31: long-polling GET on /runs/{id}, SSE
    telemetry at /events, and bearer-token auth (when tokens are
    registered or --require-auth is set).

    By default the server is wired to a real :class:`BentoLabBLE`
    instance so the device endpoints (status, /runs) work against
    actual hardware. Pass ``--no-hw`` to start in degraded mode (BLE
    reported as ``not_available``, device endpoints return empty
    results).
    """
    ble_client = None
    if not no_hw:
        # Lazy import so the ``bentolab serve --no-hw`` path doesn't
        # require bleak to be functional in the test environment.
        from ..ble_client import BentoLabBLE

        ble_client = BentoLabBLE()

    try:
        app = create_app(ble_client=ble_client, force_auth=require_auth)
    except Exception as exc:  # pragma: no cover -- construction is trivial
        fail(f"failed to construct app: {exc}", code=1)

    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )
