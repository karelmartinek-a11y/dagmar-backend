"""Utility functions for deterministic, filesystem-safe export filenames.

Requirements (DAGMAR):
- CSV filenames: {nazev_instance}_{YYYY-MM}.csv
- Must be without diacritics.
- Spaces must be replaced with '_' (underscore).
- Keep it predictable and stable.

This module intentionally avoids locale-dependent behavior.
"""

from __future__ import annotations

import re
import unicodedata


_WHITESPACE_RE = re.compile(r"\s+")
_ALLOWED_RE = re.compile(r"[^a-z0-9_-]+")
_UNDERSCORE_RUN_RE = re.compile(r"_+")
_DASH_RUN_RE = re.compile(r"-+")


def strip_diacritics(value: str) -> str:
    """Remove diacritics from unicode string.

    Example:
        "Žluťoučký kůň" -> "Zlutoucky kun"
    """
    # NFKD splits base chars and combining marks.
    normalized = unicodedata.normalize("NFKD", value)
    # Remove combining marks.
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def slugify_filename(value: str, *, max_len: int = 80) -> str:
    """Create a deterministic safe filename stem.

    Rules:
    - strip diacritics
    - lowercase
    - whitespace -> _
    - allow only [a-z0-9_-]
    - collapse repeated '_' and '-'
    - trim leading/trailing '_' and '-'

    This returns *stem* only (no extension).
    """
    if value is None:
        value = ""

    value = value.strip()
    value = strip_diacritics(value)
    value = value.lower()

    # Convert any whitespace run to underscore.
    value = _WHITESPACE_RE.sub("_", value)

    # Replace forbidden chars with underscore to keep readability.
    value = _ALLOWED_RE.sub("_", value)

    # Collapse runs.
    value = _UNDERSCORE_RUN_RE.sub("_", value)
    value = _DASH_RUN_RE.sub("-", value)

    # Trim separators.
    value = value.strip("_-")

    if not value:
        value = "instance"

    # Enforce max length.
    if max_len and len(value) > max_len:
        value = value[:max_len].rstrip("_-")
        if not value:
            value = "instance"

    return value


def filename_safe(value: str, *, max_len: int = 80) -> str:
    """Backward-compatible alias used by export endpoints."""
    return slugify_filename(value, max_len=max_len)
