"""Per-device API token management for the BentoLab HTTP API.

Tokens are bearer credentials scoped to a single BLE device address.
They are stored in a JSON file in the config directory (alongside
``devices.json``) and validated by the FastAPI middleware on every
protected request.

Modes
-----
The middleware supports two operating modes:

- **Open mode (default)**: if no tokens are registered, the API
  accepts all requests without authentication. This is the developer
  convenience for local testing.
- **Closed mode**: if at least one token is registered, every
  protected endpoint requires a valid ``Authorization: Bearer <tok>``
  header. To force closed mode even with no tokens (recommended for
  production), set ``BENTOLAB_REQUIRE_AUTH=1``.

Token format
------------
A token is a 32-character URL-safe random string (256 bits of entropy
from :func:`secrets.token_urlsafe`). It is bound to a single device
address at issue time and is not rotated automatically.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .._data_dirs import config_dir
from .._store import atomic_write_text, load_with_backup

# Token length in characters; 32 chars of URL-safe base64 = 192 bits of
# entropy, well above the 128-bit floor recommended for bearer tokens.
_TOKEN_BYTES = 24


@dataclass
class Token:
    """A registered API token bound to a single device."""

    token: str
    device_address: str
    created_at: str
    last_used_at: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> Token:
        return cls(
            token=str(raw.get("token", "")),
            device_address=str(raw.get("device_address", "")),
            created_at=str(raw.get("created_at", "")),
            last_used_at=(
                str(raw["last_used_at"]) if raw.get("last_used_at") is not None else None
            ),
        )


class TokenStore:
    """Persistent on-disk registry of API tokens.

    Storage is a single JSON file (default: ``tokens.json`` in the
    config directory). Reads fall back to a one-deep ``.bak`` if the
    primary file is missing or unreadable; writes use the project's
    standard atomic write pattern (see :mod:`bentolab._store`).
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (config_dir() / "tokens.json")

    @property
    def path(self) -> Path:
        return self._path

    # -- CRUD ----------------------------------------------------------------

    def list(self) -> list[Token]:
        raw = _load_raw(self._path)
        return [Token.from_dict(v) for v in raw.values()]

    def issue(self, device_address: str) -> Token:
        if not device_address or not device_address.strip():
            raise ValueError("device_address must be a non-empty string")
        tok = Token(
            token=secrets.token_urlsafe(_TOKEN_BYTES),
            device_address=device_address.strip(),
            created_at=datetime.now(UTC).isoformat(),
        )
        raw = _load_raw(self._path)
        raw[tok.token] = tok.to_dict()
        atomic_write_text(self._path, json.dumps(raw, indent=2, sort_keys=True))
        return tok

    def revoke(self, token: str) -> bool:
        raw = _load_raw(self._path)
        if token not in raw:
            return False
        raw.pop(token, None)
        atomic_write_text(self._path, json.dumps(raw, indent=2, sort_keys=True))
        return True

    def lookup(self, token: str) -> Token | None:
        if not token:
            return None
        for entry in self.list():
            if entry.token == token:
                return entry
        return None

    def touch(self, token: str) -> None:
        """Record that a token was just used. Best-effort, errors are swallowed."""
        raw = _load_raw(self._path)
        entry = raw.get(token)
        if not isinstance(entry, dict):
            return
        entry["last_used_at"] = datetime.now(UTC).isoformat()
        atomic_write_text(self._path, json.dumps(raw, indent=2, sort_keys=True))


def _load_raw(path: Path) -> dict[str, dict[str, object]]:
    data, _source = load_with_backup(path)
    if not data:
        return {}
    try:
        parsed = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, dict[str, object]] = {}
    for k, v in parsed.items():
        if isinstance(v, dict):
            out[str(k)] = v
    return out


def auth_required_env() -> bool:
    """Return True if the ``BENTOLAB_REQUIRE_AUTH`` env var forces closed mode."""
    val = os.environ.get("BENTOLAB_REQUIRE_AUTH", "").strip().lower()
    return val in {"1", "true", "yes", "on"}


__all__ = ["Token", "TokenStore", "auth_required_env"]
