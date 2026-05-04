"""``bentolab profile ...`` — manage YAML profiles on disk."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import typer

from .. import profiles as profile_store
from ..models import PCRProfile
from ._format import emit_json, fail, stdout

profile_app = typer.Typer(help="Manage local PCR profile library.")


@profile_app.command("list")
def list_cmd(json_output: bool = typer.Option(False, "--json")) -> None:
    """List all profiles."""
    names = profile_store.list_profiles()
    if json_output:
        emit_json(names)
        return
    if not names:
        stdout.print("[yellow]No profiles. Try `bentolab profile new <name>`.[/yellow]")
        return
    for n in names:
        stdout.print(f"  {n}")


@profile_app.command("show")
def show_cmd(
    name: str,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Print a profile."""
    try:
        profile = profile_store.load(name)
    except profile_store.ProfileNotFoundError:
        fail(f"profile not found: {name}", code=2)
    if json_output:
        emit_json(profile.to_dict())
        return
    stdout.print(profile.to_yaml())


@profile_app.command("new")
def new_cmd(
    name: str,
    no_edit: bool = typer.Option(False, "--no-edit", help="Skip $EDITOR launch."),
) -> None:
    """Create a profile from the template, then open $EDITOR."""
    if profile_store.exists(name):
        fail(f"profile already exists: {name}", code=2)
    template = profile_store.TEMPLATE_YAML.replace("name: New profile", f"name: {name}")
    if no_edit:
        try:
            profile = PCRProfile.from_yaml(template)
        except ValueError as e:
            fail(f"template invalid: {e}", code=2)
        path = profile_store.save(profile)
        stdout.print(f"[green]Created:[/green] {path}")
        return

    edited = _edit(template, suffix=".yaml")
    try:
        profile = PCRProfile.from_yaml(edited)
    except ValueError as e:
        fail(f"profile invalid: {e}", code=2)
    profile.name = name
    path = profile_store.save(profile)
    stdout.print(f"[green]Created:[/green] {path}")


@profile_app.command("edit")
def edit_cmd(name: str) -> None:
    """Open the profile YAML in $EDITOR."""
    try:
        original = profile_store.load(name)
    except profile_store.ProfileNotFoundError:
        fail(f"profile not found: {name}", code=2)
    edited = _edit(original.to_yaml(), suffix=".yaml")
    try:
        profile = PCRProfile.from_yaml(edited)
    except ValueError as e:
        fail(f"profile invalid: {e}", code=2)
    path = profile_store.save(profile, overwrite=True)
    stdout.print(f"[green]Saved:[/green] {path}")


@profile_app.command("delete")
def delete_cmd(name: str) -> None:
    """Delete a profile (does not touch device slots)."""
    try:
        profile_store.delete(name)
    except profile_store.ProfileNotFoundError:
        fail(f"profile not found: {name}", code=2)
    stdout.print(f"[green]Deleted:[/green] {name}")


@profile_app.command("import")
def import_cmd(
    path: Path,
    overwrite: bool = typer.Option(False, "--overwrite"),
) -> None:
    """Import a YAML file into the profile library."""
    if not path.exists():
        fail(f"file not found: {path}", code=2)
    try:
        profile = PCRProfile.from_yaml_file(path)
    except (OSError, ValueError) as e:
        fail(f"failed to read {path}: {e}", code=2)
    try:
        out = profile_store.save(profile, overwrite=overwrite)
    except profile_store.ProfileExistsError:
        fail(f"profile already exists: {profile.name} (use --overwrite)", code=2)
    stdout.print(f"[green]Imported:[/green] {out}")


def _edit(initial: str, *, suffix: str) -> str:
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    if not shutil.which(editor.split()[0]):
        fail(f"EDITOR not found on PATH: {editor}", code=2)
    fd, tmp_name = tempfile.mkstemp(prefix="bentolab-", suffix=suffix)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(initial)
        subprocess.run([*editor.split(), str(tmp)], check=True)  # noqa: S603
        return tmp.read_text(encoding="utf-8")
    finally:
        tmp.unlink(missing_ok=True)
