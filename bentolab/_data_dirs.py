"""Application data directories.

Wraps :mod:`platformdirs` so the rest of the codebase never hardcodes
paths. Honors ``BENTOLAB_DATA_DIR`` / ``BENTOLAB_CONFIG_DIR`` overrides
for tests and bring-your-own-storage scenarios.
"""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

_APP = "bentolab"
_AUTHOR = False  # platformdirs: omit author dir on macOS


def data_dir() -> Path:
    """Return the user-data directory (profiles, runs, devices.json)."""
    override = os.environ.get("BENTOLAB_DATA_DIR")
    base = Path(override) if override else Path(user_data_dir(_APP, appauthor=_AUTHOR))
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_dir() -> Path:
    """Return the user-config directory (config.toml)."""
    override = os.environ.get("BENTOLAB_CONFIG_DIR")
    base = Path(override) if override else Path(user_config_dir(_APP, appauthor=_AUTHOR))
    base.mkdir(parents=True, exist_ok=True)
    return base


def profiles_dir() -> Path:
    p = data_dir() / "profiles"
    p.mkdir(parents=True, exist_ok=True)
    return p


def runs_dir() -> Path:
    p = data_dir() / "runs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def devices_path() -> Path:
    return data_dir() / "devices.json"
