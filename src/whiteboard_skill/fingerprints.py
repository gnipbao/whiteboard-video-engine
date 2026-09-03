"""Stable content fingerprints for resumable pipeline artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def file_sha256(path: Path) -> str:
    """Hash a file without loading the entire asset into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_fingerprint(payload: Any) -> str:
    """Hash a JSON-compatible payload with deterministic serialization."""

    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def provider_identity(provider: object) -> str:
    """Return a stable provider implementation identifier."""

    cls = provider.__class__
    return f"{cls.__module__}.{cls.__qualname__}"
