"""FsStore — the content-addressed byte store on the local filesystem (the GCS/media replacement).

This is the "media" bytes layer: put/get a blob by its blake3 kuri. The on-disk layout mirrors the
platform's local-dir storage backend exactly::

    <store>/<book>/blake3/<ab>/<cd>/<hash>

- ``<store>``  — the root you pass to ``localdev up --store``.
- ``<book>``   — per-book namespacing (each book's bytes live under its own uuid).
- ``blake3``   — the kuri schema, as a directory (the ``://`` in a kuri can't sit in a filename, so
                 we shard on the parsed hex hash, never the raw kuri).
- ``<ab>/<cd>``— fan-out on the hash's first 4 hex chars.

Write-once + integrity-checked-on-read: a blob that already exists holds exactly these bytes
(content-addressing), so re-writes are no-ops, and ``get`` re-hashes and rejects a substituted blob.

The **local root pointer** — ``<store>/<book>/ROOT_KURI`` — is the chain-free "anchor": a plain text
file naming the book's current ``root_kuri``. Writing = put yObjs/bytes here + set this pointer; there
is no chain. The index reads this pointer live.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .kuri import blake3_kuri, kuri_parts

ROOT_POINTER_NAME = "ROOT_KURI"


class ContentError(Exception):
    """A missing kuri, a failed integrity check, or invalid input."""


class FsStore:
    """Per-store content-addressed blob store, namespaced by book uuid."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    # --- book namespacing -------------------------------------------------
    def book_dir(self, book_uuid: str) -> Path:
        return self.root / book_uuid

    def books(self) -> List[str]:
        """Every book uuid that has a directory under the store (a root pointer OR any blob shard)."""
        if not self.root.is_dir():
            return []
        return sorted(
            p.name for p in self.root.iterdir()
            if p.is_dir() and ((p / ROOT_POINTER_NAME).exists() or (p / "blake3").is_dir())
        )

    # --- blob layout ------------------------------------------------------
    def _blob_path(self, book_uuid: str, kuri: str) -> Path:
        schema, digest = kuri_parts(kuri)
        return self.book_dir(book_uuid) / schema / digest[:2] / digest[2:4] / digest

    # --- blob I/O (content-addressed) ------------------------------------
    def put(self, book_uuid: str, data: bytes) -> str:
        """Store ``data`` under its blake3 kuri; return the kuri. Idempotent (dedup by kuri)."""
        if not isinstance(data, (bytes, bytearray)):
            raise ContentError("content must be bytes")
        data = bytes(data)
        kuri = blake3_kuri(data)
        p = self._blob_path(book_uuid, kuri)
        if p.exists():
            return kuri  # write-once: identical bytes already here
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)  # atomic publish
        return kuri

    def get(self, book_uuid: str, kuri: str) -> bytes:
        """Fetch a blob by kuri, verifying integrity by re-hashing. Raises ``ContentError`` if absent
        or corrupt."""
        p = self._blob_path(book_uuid, kuri)
        if not p.exists():
            raise ContentError(f"unknown kuri {kuri} in book {book_uuid}")
        data = p.read_bytes()
        actual = blake3_kuri(data)
        if actual != kuri:
            raise ContentError(f"integrity check failed: stored bytes hash to {actual}, not {kuri}")
        return data

    def has(self, book_uuid: str, kuri: str) -> bool:
        return self._blob_path(book_uuid, kuri).exists()

    # --- the chain-free root pointer (the local "anchor") ----------------
    def _root_pointer(self, book_uuid: str) -> Path:
        return self.book_dir(book_uuid) / ROOT_POINTER_NAME

    def get_root(self, book_uuid: str) -> Optional[str]:
        """The book's current ``root_kuri`` from the local pointer, or None if never anchored."""
        p = self._root_pointer(book_uuid)
        if not p.exists():
            return None
        val = p.read_text(encoding="utf-8").strip()
        return val or None

    def set_root(self, book_uuid: str, root_kuri: str) -> None:
        """Set the local root pointer — the no-op/local anchor. The root yObj must already be stored
        (content-addressed) so the index can walk it; we verify that to catch a dangling anchor early."""
        if not self.has(book_uuid, root_kuri):
            raise ContentError(
                f"cannot anchor {root_kuri}: its bytes are not in the store for book {book_uuid} "
                f"(push the root list yObj before anchoring)")
        p = self._root_pointer(book_uuid)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(root_kuri, encoding="utf-8")
        tmp.replace(p)
