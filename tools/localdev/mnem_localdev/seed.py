"""Seed a small LBFS tree into the FS store — the write primitive + a demo/test fixture builder.

``build_tree`` is the chain-free write path in miniature: given ``{logical_path: leaf_bytes}`` it
stores each leaf blob content-addressed, builds one ``/ytypes/list/`` folder yObj per directory
(cascading bottom-up so a parent embeds its children's kuris), stores every folder yObj, sets the
local root pointer to the top folder's kuri — and returns that ``root_kuri``. No chain.

This is exactly what an app's kilai/commit path does when pointed at the local media + index seams:
push bytes (``POST /<book>/objects``), push the folder yObjs, then anchor the new root
(``POST /books/<book>/root_kuri``). ``build_tree`` does it in-process for tests and demos; ``add_leaf``
shows the incremental single-file write.
"""
from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Tuple

from .store import FsStore
from .tree import Entry, LIST_YTYPE, build_list_yobj, walk_leaves

LEAF_YTYPE = "/ytypes/document/"   # a generic leaf ytype; the tool serves whatever bytes you store


def _norm(path: str) -> str:
    return "/" + path.strip("/")


def _split_dirs(paths) -> Dict[str, dict]:
    """Group leaf paths into a nested {dirpath: {"dirs": set, "leaves": {name: path}}} structure."""
    tree: Dict[str, dict] = {"/": {"dirs": set(), "leaves": {}}}

    def _ensure(d: str):
        if d not in tree:
            tree[d] = {"dirs": set(), "leaves": {}}
            parent = d.rsplit("/", 1)[0] or "/"
            _ensure(parent)
            tree[parent]["dirs"].add(d)

    for p in paths:
        p = _norm(p)
        parent = p.rsplit("/", 1)[0] or "/"
        name = p.rsplit("/", 1)[-1]
        _ensure(parent)
        tree[parent]["leaves"][name] = p
    return tree


def build_tree(store: FsStore, book_uuid: str, leaves: Mapping[str, bytes], *,
               anchor: bool = True) -> str:
    """Store ``leaves`` (``{logical_path: bytes}``) as an LBFS tree and return the ``root_kuri``.

    Cascades bottom-up: deepest folders first, so each parent folder yObj can embed the already-known
    child kuris. If ``anchor`` (default), also sets the local root pointer — the chain-free anchor."""
    dirtree = _split_dirs(leaves.keys())

    # 1. store every leaf blob, remember its kuri + size
    leaf_kuri: Dict[str, Tuple[str, int]] = {}
    for path, data in leaves.items():
        data = bytes(data)
        leaf_kuri[_norm(path)] = (store.put(book_uuid, data), len(data))

    # 2. cascade folders deepest-first so children are computed before parents. Depth = segment count,
    #    so root "/" (0) sorts strictly below any "/x" (1) — count("/") would tie them.
    def _depth(d: str) -> int:
        return len([s for s in d.strip("/").split("/") if s])

    folder_kuri: Dict[str, str] = {}
    for folder in sorted(dirtree, key=_depth, reverse=True):
        node = dirtree[folder]
        entries: List[Entry] = []
        for sub in sorted(node["dirs"]):
            entries.append(Entry(name=sub.rsplit("/", 1)[-1], ytype=LIST_YTYPE, yobj=sub,
                                 label=sub, kuri=folder_kuri[sub]))
        for name in sorted(node["leaves"]):
            leaf_path = node["leaves"][name]
            k, size = leaf_kuri[leaf_path]
            entries.append(Entry(name=name, ytype=LEAF_YTYPE, yobj=leaf_path,
                                 label=leaf_path, kuri=k, size=size))
        yobj = build_list_yobj(folder, entries)
        folder_kuri[folder] = store.put(book_uuid, _canonical_bytes(yobj))

    root = folder_kuri["/"]
    if anchor:
        store.set_root(book_uuid, root)
    return root


def _canonical_bytes(yobj: dict) -> bytes:
    from .kuri import canonical_json
    return canonical_json(yobj).encode("utf-8")


def add_leaf(store: FsStore, book_uuid: str, path: str, data: bytes, *, anchor: bool = True) -> str:
    """Add/replace ONE leaf and re-anchor — the incremental write. Reads the current tree, folds in the
    new leaf, rebuilds, and returns the new ``root_kuri``. (Simple O(book) rebuild — fine for local dev.)"""
    root = store.get_root(book_uuid)
    leaves: Dict[str, bytes] = {}
    if root is not None:
        for lp, k in walk_leaves(store, book_uuid, root):
            leaves[lp] = store.get(book_uuid, k)
    leaves[_norm(path)] = bytes(data)
    return build_tree(store, book_uuid, leaves, anchor=anchor)
