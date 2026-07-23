"""Filesystem-safe slug generation.

Single source of truth for turning user-facing names into filesystem-
safe slugs. Previously two implementations lived in
:mod:`bentolab.profiles` (regex, more robust) and
:mod:`bentolab._logging` (char-by-char, collapses differently for
runs of bad characters). They diverge for inputs like ``"a!!!b"``:

  regex:   ``"a-b"``   (collapses runs of bad chars)
  char-by: ``"a---b"`` (each bad char becomes its own dash)

The regex version is more robust and is now the single implementation
used by both call sites.
"""

from __future__ import annotations

import re

# Allowed: ASCII letters/digits, dot, underscore, hyphen. Anything
# else is collapsed into a single hyphen.
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def slug_for(name: str) -> str:
    """Return a filesystem-safe slug for ``name``.

    Whitespace and special characters are replaced with hyphens. Runs
    of disallowed characters collapse into a single hyphen. Leading
    and trailing hyphens are stripped.

    Raises:
        ValueError: if the resulting slug is empty (e.g. the input was
            all-special characters).
    """
    s = _SLUG_RE.sub("-", name).strip("-")
    if not s:
        raise ValueError(f"Name {name!r} produced an empty slug")
    return s


__all__ = ["slug_for"]
