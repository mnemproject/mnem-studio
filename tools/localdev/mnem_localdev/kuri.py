"""Content-addressing primitives — byte-compatible with the mnem platform.

A **kuri** is self-describing: ``<schema>://<hash>`` (e.g. ``blake3://<hex>``). The schema travels
with the identifier so a stored kuri is never ambiguous. Content-addressing makes writes idempotent
(identical bytes -> identical kuri -> dedup) and lets a read verify integrity by re-hashing.

The hash is **blake3** and the canonical JSON is **sorted-keys, no whitespace** — the exact same
conventions the platform uses (``content.store.blake3_kuri`` / ``content.tree.canonical_json``), so a
yObj hashes to the SAME kuri here as it does in production. That byte-for-byte match is what makes
pointing an app at this local server a pure URL swap.

The only non-stdlib dependency in the whole tool lives here: ``blake3``. Any machine that runs a mnem
app already has it (the app itself content-addresses with blake3), so this adds nothing new to install.
"""
from __future__ import annotations

import json
from typing import Tuple

try:
    import blake3 as _blake3
except ImportError as exc:  # pragma: no cover - environment guard
    raise ImportError(
        "mnem-localdev needs the 'blake3' package (the same hash the mnem platform uses for kuris). "
        "Install it with:  pip install blake3"
    ) from exc

KURI_SCHEME = "blake3"


def blake3_hex(data: bytes) -> str:
    """The blake3 hex digest of ``data``."""
    return _blake3.blake3(bytes(data)).hexdigest()


def blake3_kuri(data: bytes) -> str:
    """The canonical kuri for ``data``: ``blake3://<hex>``."""
    return f"{KURI_SCHEME}://{blake3_hex(data)}"


def canonical_json(obj) -> str:
    """The one canonical JSON serialization — sorted keys, no whitespace — the exact bytes a yObj is
    hashed over and stored as. Byte-identical to the platform's ``canonical_json``."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def yobj_kuri(yobj) -> str:
    """The content address of a yObj: ``blake3://<hex>`` of its canonical JSON."""
    return blake3_kuri(canonical_json(yobj).encode("utf-8"))


class KuriError(ValueError):
    """A malformed kuri, or a path that does not encode one."""


def kuri_parts(kuri: str) -> Tuple[str, str]:
    """Split ``<schema>://<hash>`` into ``(schema, hash)``. Raises so a bare hex digest can never
    silently masquerade as a kuri."""
    schema, sep, digest = kuri.partition("://")
    if not sep or not schema or not digest:
        raise KuriError(f"malformed kuri (expected '<schema>://<hash>'): {kuri!r}")
    return schema, digest


def is_kuri(s: str) -> bool:
    """True iff ``s`` is a self-describing kuri (``<schema>://<hash>``) rather than a logical path."""
    try:
        kuri_parts(s)
        return True
    except KuriError:
        return False


# --- the files/ byte-path convention (the direct serve-by-kuri route) ---
YDATA_SUFFIX = ".ydata"
FILES_PREFIX = "files/"


def files_path(kuri: str) -> str:
    """``kuri`` -> ``files/blake3/<ab>/<cd>/<hash>.ydata`` (book-relative). The fan-out shards on the
    hash's first 4 hex chars, matching the on-disk store layout so the same kuri maps to the same
    place everywhere."""
    schema, digest = kuri_parts(kuri)
    return f"{FILES_PREFIX}{schema}/{digest[:2]}/{digest[2:4]}/{digest}{YDATA_SUFFIX}"


def is_files_path(path: str) -> bool:
    """True iff ``path`` is a ``files/...`` byte path (tolerant of a leading ``/``)."""
    return path.lstrip("/").startswith(FILES_PREFIX)


def kuri_from_files_path(path: str) -> str:
    """Parse the kuri back out of a (book-relative) ``files/<schema>/<ab>/<cd>/<hash>.ydata`` path.
    Accepts a leading ``/``. Raises ``KuriError`` if it isn't a well-formed files path."""
    rel = path.lstrip("/")
    if not rel.startswith(FILES_PREFIX):
        raise KuriError(f"not a files/ path: {path!r}")
    parts = rel[len(FILES_PREFIX):].split("/")
    if len(parts) < 2:
        raise KuriError(f"malformed files/ path: {path!r}")
    schema = parts[0]
    digest = parts[-1]
    if digest.endswith(YDATA_SUFFIX):
        digest = digest[: -len(YDATA_SUFFIX)]
    kuri = f"{schema}://{digest}"
    if not is_kuri(kuri):
        raise KuriError(f"files/ path does not encode a kuri: {path!r}")
    return kuri
