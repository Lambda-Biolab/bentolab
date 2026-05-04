"""Atomic file write with a one-deep on-disk backup.

Pattern (borrowed from SpliceCraft): copy the prior file to ``*.bak``,
then write a temp file in the target directory, ``fsync`` it, and
``os.replace`` it onto the target path. Mid-process crashes leave the
``.bak`` intact for recovery.

:func:`load_with_backup` returns ``(data, source)`` where ``source`` is
``"primary"`` or ``"backup"`` so callers can warn the user when
falling back.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Literal


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically write ``data`` to ``path``, rotating any existing file to ``*.bak``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)

    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


def load_with_backup(path: Path) -> tuple[bytes, Literal["primary", "backup", "missing"]]:
    """Read ``path``; on failure fall back to ``path.bak``.

    Returns ``(b"", "missing")`` if neither file exists. Raises only if
    a file exists but cannot be read (permission errors, etc.).
    """
    path = Path(path)
    if path.exists():
        return path.read_bytes(), "primary"
    backup = path.with_suffix(path.suffix + ".bak")
    if backup.exists():
        return backup.read_bytes(), "backup"
    return b"", "missing"
