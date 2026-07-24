"""``bentolab token ...`` — manage API tokens for the HTTP API."""

from __future__ import annotations

import typer

from ..api.auth import TokenStore
from ._format import emit_json, fail, stdout

token_app = typer.Typer(help="Manage API tokens for the BentoLab HTTP API.")


@token_app.command("issue")
def issue_cmd(
    device: str = typer.Option(..., "--device", help="BLE address this token is bound to."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON to stdout."),
) -> None:
    """Issue a new API token for a device.

    The token is printed once. Store it securely — there is no way
    to recover it after this command returns.
    """
    store = TokenStore()
    try:
        tok = store.issue(device)
    except ValueError as exc:
        fail(str(exc), code=2)

    if json_output:
        emit_json(tok.to_dict())
        return
    stdout.print(tok.token)


@token_app.command("list")
def list_cmd(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON to stdout."),
) -> None:
    """List all issued tokens (one row per token)."""
    store = TokenStore()
    tokens = store.list()
    if json_output:
        emit_json([t.to_dict() for t in tokens])
        return
    if not tokens:
        stdout.print("[yellow]No tokens. Try `bentolab token issue --device ADDR`.[/yellow]")
        return
    for t in tokens:
        last = t.last_used_at or "never"
        stdout.print(
            f"  {t.token[:8]}\u2026  device={t.device_address}  "
            f"created={t.created_at}  last_used={last}"
        )


@token_app.command("revoke")
def revoke_cmd(
    token: str = typer.Argument(..., help="Token to revoke."),
) -> None:
    """Revoke a token. Future requests bearing it return 401."""
    store = TokenStore()
    if not store.revoke(token):
        fail("token not found", code=2)
    stdout.print("[green]Revoked.[/green]")
