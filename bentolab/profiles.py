"""Profile library — YAML files in the user data dir.

Single shared store between the CLI and the TUI. Atomic write + ``.bak``
recovery on every save. Profile name is the canonical identifier; the
on-disk filename is a slug derived from the name.
"""

from __future__ import annotations

import re
from pathlib import Path

from ._data_dirs import profiles_dir
from ._store import atomic_write_text, load_with_backup
from .models import PCRProfile


class ProfileNotFoundError(KeyError):
    pass


class ProfileExistsError(FileExistsError):
    pass


_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def slug_for(name: str) -> str:
    """Filesystem-safe slug for a profile name."""
    s = _SLUG_RE.sub("-", name).strip("-")
    if not s:
        raise ValueError("Profile name produced an empty slug")
    return s


def _path_for(name: str, *, root: Path | None = None) -> Path:
    base = root or profiles_dir()
    return base / f"{slug_for(name)}.yaml"


def list_profiles(*, root: Path | None = None) -> list[str]:
    """Return profile names in lexical filename order."""
    base = root or profiles_dir()
    names: list[str] = []
    for path in sorted(base.glob("*.yaml")):
        try:
            profile = PCRProfile.from_yaml_file(path)
        except (OSError, ValueError):
            continue
        names.append(profile.name)
    return names


def load(name: str, *, root: Path | None = None) -> PCRProfile:
    path = _path_for(name, root=root)
    data, source = load_with_backup(path)
    if source == "missing":
        raise ProfileNotFoundError(name)
    return PCRProfile.from_yaml(data.decode("utf-8"))


def save(profile: PCRProfile, *, overwrite: bool = False, root: Path | None = None) -> Path:
    path = _path_for(profile.name, root=root)
    if path.exists() and not overwrite:
        raise ProfileExistsError(profile.name)
    atomic_write_text(path, profile.to_yaml())
    return path


def delete(name: str, *, root: Path | None = None) -> None:
    path = _path_for(name, root=root)
    if not path.exists():
        raise ProfileNotFoundError(name)
    path.unlink()
    backup = path.with_suffix(path.suffix + ".bak")
    backup.unlink(missing_ok=True)


def exists(name: str, *, root: Path | None = None) -> bool:
    return _path_for(name, root=root).exists()


TEMPLATE_YAML = """\
name: New profile
lid_temperature: 110
initial_denaturation:
  temperature: 95
  duration: 300
cycles:
  - repeat: 30
    denaturation: { temperature: 98, duration: 10 }
    annealing:    { temperature: 60, duration: 30 }
    extension:    { temperature: 72, duration: 60 }
final_extension:
  temperature: 72
  duration: 300
hold_temperature: 4
notes: ""
"""
