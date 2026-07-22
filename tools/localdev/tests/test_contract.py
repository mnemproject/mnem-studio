"""Unit tests: the FS store, the LBFS tree walk, kuri byte-compatibility, and the resolver."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mnem_localdev.kuri import (blake3_kuri, canonical_json, files_path, is_kuri,
                                kuri_from_files_path, yobj_kuri)
from mnem_localdev.seed import build_tree
from mnem_localdev.server import FsResolver
from mnem_localdev.store import ContentError, FsStore
from mnem_localdev import tree

BOOK = "11111111-1111-4111-8111-111111111111"


def test_canonical_json_is_sorted_compact():
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_kuri_scheme_and_layout(tmp_path):
    store = FsStore(tmp_path)
    kuri = store.put(BOOK, b"hello")
    assert kuri.startswith("blake3://")
    assert kuri == blake3_kuri(b"hello")
    _, digest = kuri.split("://")
    on_disk = tmp_path / BOOK / "blake3" / digest[:2] / digest[2:4] / digest
    assert on_disk.exists() and on_disk.read_bytes() == b"hello"


def test_put_is_idempotent_and_get_verifies(tmp_path):
    store = FsStore(tmp_path)
    k1 = store.put(BOOK, b"same bytes")
    k2 = store.put(BOOK, b"same bytes")
    assert k1 == k2
    assert store.get(BOOK, k1) == b"same bytes"


def test_get_rejects_corrupt_blob(tmp_path):
    store = FsStore(tmp_path)
    kuri = store.put(BOOK, b"original")
    _, digest = kuri.split("://")
    (tmp_path / BOOK / "blake3" / digest[:2] / digest[2:4] / digest).write_bytes(b"tampered")
    with pytest.raises(ContentError):
        store.get(BOOK, kuri)


def test_files_path_roundtrip():
    kuri = blake3_kuri(b"abc")
    fp = files_path(kuri)
    assert fp.startswith("files/blake3/") and fp.endswith(".ydata")
    assert kuri_from_files_path(fp) == kuri
    assert is_kuri(kuri) and not is_kuri("/posts/first")


def test_build_tree_and_walk(tmp_path):
    store = FsStore(tmp_path)
    leaves = {
        "/readme.txt": b"top-level readme\n",
        "/posts/a.txt": b"post a\n",
        "/posts/b.txt": b"post b\n",
        "/media/sub/x.txt": b"nested\n",
    }
    root = build_tree(store, BOOK, leaves)
    assert store.get_root(BOOK) == root
    # root yObj is a /ytypes/list/ yObj whose kuri matches its canonical JSON
    root_yobj = json.loads(store.get(BOOK, root).decode())
    assert root_yobj["ytype"] == "/ytypes/list/"
    assert yobj_kuri(root_yobj) == root
    # walk_leaves reconstructs the full path->kuri set from the root alone
    walked = dict(tree.walk_leaves(store, BOOK, root))
    assert set(walked) == set(leaves)
    for lp, k in walked.items():
        assert store.get(BOOK, k) == leaves[lp]


def test_resolver_root_and_children(tmp_path):
    store = FsStore(tmp_path)
    leaves = {"/readme.txt": b"r\n", "/posts/a.txt": b"a\n", "/posts/b.txt": b"b\n"}
    root = build_tree(store, BOOK, leaves)
    resolver = FsResolver(store)
    assert resolver.root_kuri(BOOK) == root

    entries, cursor = resolver.children(BOOK, "/", limit=500, after=None)
    paths = {e["path"]: e for e in entries}
    assert paths["/posts"]["is_folder"] is True
    assert paths["/readme.txt"]["is_folder"] is False
    assert cursor is None

    posts, _ = resolver.children(BOOK, "/posts", limit=500, after=None)
    assert sorted(e["path"] for e in posts) == ["/posts/a.txt", "/posts/b.txt"]


def test_children_pagination(tmp_path):
    store = FsStore(tmp_path)
    leaves = {f"/items/f{i:02d}.txt": f"item {i}\n".encode() for i in range(5)}
    root = build_tree(store, BOOK, leaves)
    resolver = FsResolver(store)
    seen, cursor = [], None
    while True:
        page, cursor = resolver.children(BOOK, "/items", limit=2, after=cursor)
        seen += [e["path"] for e in page]
        if cursor is None:
            break
    assert seen == sorted(leaves)


def test_unknown_book_root_is_none(tmp_path):
    resolver = FsResolver(FsStore(tmp_path))
    assert resolver.root_kuri("nope") is None
