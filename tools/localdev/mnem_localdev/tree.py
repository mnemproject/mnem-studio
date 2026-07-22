"""The LBFS tree — build ``/ytypes/list/`` folder yObjs and NAVIGATE them live off the filesystem.

This is the chain-free heart of the index. In production the master index projects a ``{path: kuri}``
map per book by tree-walking a chain-anchored ``root_kuri``; here we do the SAME tree-walk, live, over
the FS store — no chain, no projection cache. A folder is a ``/ytypes/list/`` yObj whose ``items[]``
reference its children (subfolders and file leaves), each carrying the child's ``kuri``. Reading a
folder is ONE ``store.get`` (its direct children); resolving a path is one get per level (O(depth)).

Byte-compatibility: ``build_list_yobj`` emits the exact field set + canonical JSON the platform's
``content.tree`` does, so a folder yObj hashes to the SAME kuri here as in production.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable, Iterator, List, Optional, Tuple
from uuid import UUID, uuid5

from .kuri import yobj_kuri
from .store import ContentError, FsStore

LIST_YTYPE = "/ytypes/list/"
# Deterministic uuid5 of the folder label (base requires uuid; keeps the bytes reproducible) — the same
# namespace the platform's content.tree uses, so folder yObjs match byte-for-byte.
_LIST_UUID_NAMESPACE = UUID("4c0f9d2a-6b18-4e3a-9f55-1d2c3b4a5e6f")
_NAME_RE = re.compile(r"[^A-Za-z0-9_]+")


class NotInTree(Exception):
    """A path doesn't exist in the tree, or a kuri isn't a folder (``/ytypes/list/``) yObj."""


# --------------------------------------------------------------------------
# Build a folder list yObj (the write side)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Entry:
    """A child reference in a folder listing: a subfolder (another list yObj) or a file leaf."""

    name: str
    ytype: str
    yobj: str
    kuri: Optional[str] = None
    label: Optional[str] = None
    size: Optional[int] = None


def _ytype_name(ytype: str) -> str:
    return ytype.strip("/").split("/")[-1] or "yobj"


def _safe_name(label: str) -> str:
    seg = label.rstrip("/").split("/")[-1]
    return _NAME_RE.sub("_", seg).strip("_") or "root"


def _list_item(entry: Entry, order: int) -> dict:
    name = _NAME_RE.sub("_", entry.name).strip("_") or "item"
    ytn = _ytype_name(entry.ytype)
    item = {
        "yobj_name": name,
        "ytype": entry.ytype,
        "ytype_name": ytn,
        "ytype_label": ytn.title(),
        "yobj": entry.yobj,
        "yobj_label": entry.label or entry.yobj,
        "order": order,
    }
    if entry.kuri:
        item["kuri"] = entry.kuri  # the Merkle link: child content address embedded in the parent
    if entry.size is not None:
        item["size"] = entry.size
    return item


def build_list_yobj(folder_label: str, entries: Iterable[Entry]) -> dict:
    """A schema-correct ``/ytypes/list/`` yObj for a folder from its child entries. The caller
    pre-sorts entries so the folder hash is deterministic."""
    items = [_list_item(e, i) for i, e in enumerate(entries)]
    return {
        "ytype": LIST_YTYPE,
        "ytype_label": "List",
        "name": _safe_name(folder_label),
        "label": folder_label,
        "description": f"Folder listing for {folder_label}.",
        "uuid": str(uuid5(_LIST_UUID_NAMESPACE, folder_label)),
        "items": items,
        "graph": [],
    }


# --------------------------------------------------------------------------
# Navigate the tree (the read side) — reads yObjs live from the FS store
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Child:
    name: str
    label: str          # the child's logical path
    ytype: str
    kuri: Optional[str]
    is_folder: bool
    size: int


def _leaf_segment(label: str) -> str:
    return label.rstrip("/").rsplit("/", 1)[-1]


def read_folder(store: FsStore, book_uuid: str, folder_kuri: str) -> dict:
    """Load + parse a folder's ``/ytypes/list/`` yObj by its kuri. Raises ``NotInTree`` if the bytes
    are not a list yObj (e.g. a leaf blob)."""
    try:
        raw = store.get(book_uuid, folder_kuri)
    except ContentError as exc:
        raise NotInTree(str(exc)) from exc
    try:
        yobj = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NotInTree(f"{folder_kuri} is not a folder yObj (not list JSON)") from exc
    if not isinstance(yobj, dict) or yobj.get("ytype") != LIST_YTYPE:
        raise NotInTree(f"{folder_kuri} is not a folder yObj (ytype {yobj.get('ytype')!r})")
    return yobj


def children(store: FsStore, book_uuid: str, folder_kuri: str) -> List[Child]:
    """The direct children of a folder (one ``store.get``)."""
    items = read_folder(store, book_uuid, folder_kuri).get("items", [])
    return [
        Child(
            name=item["yobj_name"],
            label=item.get("yobj_label", item["yobj_name"]),
            ytype=item["ytype"],
            kuri=item.get("kuri"),
            is_folder=item["ytype"] == LIST_YTYPE,
            size=item.get("size", 0),
        )
        for item in items
    ]


def resolve(store: FsStore, book_uuid: str, root_kuri: str, path: str = "/") -> str:
    """Resolve a logical path to the kuri of the folder/file at it, walking one level at a time
    (O(depth)). ``"/"`` returns ``root_kuri``. Raises ``NotInTree`` if a segment is absent."""
    kuri = root_kuri
    walked: List[str] = []
    for seg in [s for s in path.strip("/").split("/") if s]:
        match = next((c for c in children(store, book_uuid, kuri) if _leaf_segment(c.label) == seg), None)
        if match is None or match.kuri is None:
            raise NotInTree(f"no '{seg}' under /{'/'.join(walked)}")
        kuri = match.kuri
        walked.append(seg)
    return kuri


def list_folder(store: FsStore, book_uuid: str, root_kuri: str, path: str = "/") -> List[Child]:
    """The direct children of the folder at ``path`` (O(folder))."""
    return children(store, book_uuid, resolve(store, book_uuid, root_kuri, path))


def walk_leaves(store: FsStore, book_uuid: str, root_kuri: str) -> Iterator[Tuple[str, str]]:
    """Yield ``(logical_path, kuri)`` for every file leaf reachable from ``root_kuri`` (depth-first)."""

    def _walk(folder_kuri: str) -> Iterator[Tuple[str, str]]:
        for c in children(store, book_uuid, folder_kuri):
            if c.is_folder:
                if c.kuri:
                    yield from _walk(c.kuri)
            elif c.kuri:
                yield c.label, c.kuri

    yield from _walk(root_kuri)


# --------------------------------------------------------------------------
# The /list read surface: a bounded, path-keyset-paginated page of children
# --------------------------------------------------------------------------
def _normalize(path: str) -> str:
    return "/" if path in ("", "/") else "/" + path.strip("/")


def page_children(store: FsStore, book_uuid: str, root_kuri: str, folder: str, *,
                  limit: int, after: Optional[str]) -> Tuple[List[dict], Optional[str]]:
    """A bounded page of ``folder``'s direct children, PATH-KEYSET paginated — the exact shape the
    platform index returns. ``(entries, next_cursor)`` where ``entries = [{path, kuri, is_folder}]``
    sorted by path, at most ``limit``; ``next_cursor`` is the last path when more remain, else None.

    Chain-free: resolves ``folder`` against ``root_kuri`` by walking the FS tree live, then pages the
    resolved folder's children. Raises ``NotInTree`` if the folder is absent (the caller maps that to
    an empty listing, mirroring the app's graceful-empty read)."""
    folder = _normalize(folder)
    kids = children(store, book_uuid, resolve(store, book_uuid, root_kuri, folder))
    rows = sorted(
        ({"path": c.label.rstrip("/"), "kuri": c.kuri, "is_folder": c.is_folder}
         for c in kids if c.kuri is not None),
        key=lambda r: r["path"],
    )
    rows = [r for r in rows if after is None or r["path"] > after]
    page = rows[:limit]
    next_cursor = page[-1]["path"] if len(rows) > limit else None
    return page, next_cursor
