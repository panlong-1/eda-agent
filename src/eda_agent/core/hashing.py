"""sha256 in one place: of a file, of bytes, and of JSON data (a receipt's content, a snapshot).

Every recorded hash is a plain hex sha256, so a reader can recompute it with `sha256sum`.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str | None:
    """The file's sha256, or None when there is no such file."""
    p = Path(path)
    return sha256_bytes(p.read_bytes()) if p.is_file() else None


def sha256_json(obj, *, ensure_ascii: bool = True, default=None) -> str:
    """sha256 of the JSON text of obj with sorted keys: the same data gives the same hash."""
    return sha256_bytes(json.dumps(obj, sort_keys=True, ensure_ascii=ensure_ascii, default=default).encode())


def content_sha256(record: dict, leave_out: tuple[str, ...] = ()) -> str:
    """Hash of a record (a receipt) without the fields in leave_out and without "_" display keys,
    so it can be re-checked on the saved record."""
    body = {k: v for k, v in record.items() if k not in leave_out and not k.startswith("_")}
    return sha256_json(body, ensure_ascii=False)
