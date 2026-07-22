"""Smoke test: START the servers on real ports, exercise the wire contract over HTTP, and prove a
write roundtrip lands live. Also drives the REAL platform read client (``IndexBook``) when a local
checkout of the platform source is pointed at via ``MNEM_PLATFORM_SRC`` (otherwise an equivalent
client, which mirrors IndexBook's exact HTTP calls, stands in).
"""
import json
import os
import socket
import sys
import urllib.request
from pathlib import Path
from urllib.parse import quote

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mnem_localdev.seed import build_tree
from mnem_localdev.server import make_index_server, make_media_server, serve_in_thread
from mnem_localdev.store import FsStore

BOOK = "22222222-2222-4222-8222-222222222222"
CHANNEL = 0  # chain-free: channel id is inert here, kept only to match the app's call signature
LEAVES = {
    "/readme.txt": b"local dev readme\n",
    "/posts/2024-01/first.txt": b"the first post\n",
    "/posts/2024-01/second.txt": b"the second post\n",
}


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _get(url, want_json=True):
    with urllib.request.urlopen(url, timeout=10) as r:
        body = r.read()
        return (r.status, json.loads(body) if want_json else body)


def _get_status_json(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _post_bytes(url, data):
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/octet-stream"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read().decode().strip()


def _post_json(url, obj):
    req = urllib.request.Request(url, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read())


@pytest.fixture()
def stack(tmp_path):
    store = FsStore(tmp_path)
    build_tree(store, BOOK, dict(LEAVES))
    iport, mport = _free_port(), _free_port()
    index = make_index_server(store, iport)
    media = make_media_server(store, mport)
    serve_in_thread(index)
    serve_in_thread(media)
    try:
        yield store, f"http://127.0.0.1:{iport}", f"http://127.0.0.1:{mport}"
    finally:
        index.shutdown()
        media.shutdown()


def test_health(stack):
    _store, index_url, media_url = stack
    assert _get(f"{index_url}/health") == (200, {"ok": True})
    assert _get(f"{media_url}/health") == (200, {"ok": True})


def test_root_kuri_and_unknown_book(stack):
    store, index_url, _media = stack
    status, body = _get(f"{index_url}/books/{BOOK}/root_kuri")
    assert status == 200 and body["root_kuri"] == store.get_root(BOOK)
    status, body = _get_status_json(f"{index_url}/books/does-not-exist/root_kuri")
    assert status == 404 and body["detail"] == "unknown book"


def test_list_contract(stack):
    _store, index_url, _media = stack
    status, body = _get(f"{index_url}/books/{BOOK}/list?folder=/")
    assert status == 200
    assert set(body) == {"book_uuid", "folder", "next_cursor", "entries"}
    byp = {e["path"]: e for e in body["entries"]}
    assert byp["/posts"]["is_folder"] is True
    assert byp["/readme.txt"]["is_folder"] is False and byp["/readme.txt"]["kuri"].startswith("blake3://")


def test_fetch_leaf_by_kuri_and_files_path(stack):
    _store, index_url, media_url = stack
    # resolve /readme.txt -> kuri via the index listing, then fetch bytes both ways from media
    _s, body = _get(f"{index_url}/books/{BOOK}/list?folder=/")
    kuri = next(e["kuri"] for e in body["entries"] if e["path"] == "/readme.txt")
    # (a) serve-by-kuri: GET /<book>/objects/<kuri>
    _st, data = _get(f"{media_url}/{BOOK}/objects/{quote(kuri, safe='')}", want_json=False)
    assert data == LEAVES["/readme.txt"]
    # (b) files/blake3 byte path
    _, digest = kuri.split("://")
    fp = f"files/blake3/{digest[:2]}/{digest[2:4]}/{digest}.ydata"
    _st, data2 = _get(f"{media_url}/{BOOK}/{fp}", want_json=False)
    assert data2 == LEAVES["/readme.txt"]


def test_write_roundtrip_lands_live(stack):
    store, index_url, media_url = stack
    # 1. push new bytes via the media accept route -> derived kuri
    new_bytes = b"a freshly pushed post\n"
    status, derived = _post_bytes(f"{media_url}/{BOOK}/objects", new_bytes)
    assert status == 200 and derived.startswith("blake3://")
    assert store.get(BOOK, derived) == new_bytes
    # 2. rebuild the tree including the new leaf and ANCHOR via the chain-free anchor seam
    leaves = dict(LEAVES)
    leaves["/posts/2024-01/third.txt"] = new_bytes
    new_root = build_tree(store, BOOK, leaves, anchor=False)
    status, body = _post_json(f"{index_url}/books/{BOOK}/root_kuri", {"root_kuri": new_root})
    assert status == 200 and body["root_kuri"] == new_root
    # 3. the index now serves the new leaf LIVE (no chain, no restart)
    _s, listing = _get(f"{index_url}/books/{BOOK}/list?folder=/posts/2024-01")
    assert "/posts/2024-01/third.txt" in {e["path"] for e in listing["entries"]}


# --------------------------------------------------------------------------
# Contract-match against the REAL app read path (mnembyte IndexBook) or an equivalent client
# --------------------------------------------------------------------------
class _MediaArchiver:
    """The duck-typed ``archiver`` IndexBook calls: fetch bytes by kuri from the media gateway.
    Chain-free — nothing is sealed, so ``retrieve`` returns the bytes as-is (no unseal)."""

    def __init__(self, media_url):
        self.media_url = media_url.rstrip("/")

    def retrieve(self, kuri, channel_id, *, key=None):
        url = f"{self.media_url}/{BOOK}/objects/{quote(kuri, safe='')}"
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.read()


def _load_real_index_book():
    src = os.environ.get("MNEM_PLATFORM_SRC")
    if not src:
        return None
    for sub in ("services/content", "services/mnembyte"):
        p = str(Path(src) / sub)
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        from mnembyte.index_book import IndexBook  # type: ignore
        return IndexBook
    except Exception:  # noqa: BLE001 - optional; fall back to the equivalent client
        return None


class _EquivalentIndexBook:
    """Mirrors mnembyte.index_book.IndexBook's exact HTTP calls (root_kuri / list / resolve / retrieve),
    so this stands in when the real source isn't present. Same method surface, same URLs, same shapes."""

    def __init__(self, *, book_uuid, channel_id, index_base, archiver, get_json):
        self.book_uuid = book_uuid
        self.channel_id = channel_id
        self._index = index_base.rstrip("/")
        self.archiver = archiver
        self._get_json = get_json

    @property
    def root_kuri(self):
        st, body = self._get_json(f"{self._index}/books/{self.book_uuid}/root_kuri")
        return body.get("root_kuri") if st == 200 and isinstance(body, dict) else None

    def list(self, path="/"):
        norm = "/" if path in ("", "/") else path.rstrip("/")
        st, body = self._get_json(f"{self._index}/books/{self.book_uuid}/list?folder={quote(norm)}")
        assert st == 200
        return body["entries"]

    def resolve(self, path):
        norm = path.rstrip("/")
        parent = norm.rsplit("/", 1)[0] or "/"
        for e in self.list(parent):
            if e["path"].rstrip("/") == norm and not e["is_folder"]:
                return e["kuri"]
        raise KeyError(path)

    def retrieve(self, path, *, key=None):
        return self.archiver.retrieve(self.resolve(path), self.channel_id, key=key)


def test_real_app_read_path(stack):
    store, index_url, media_url = stack
    IndexBook = _load_real_index_book()
    archiver = _MediaArchiver(media_url)
    get_json = _get_status_json  # IndexBook needs (status, json) from every GET
    if IndexBook is not None:
        book = IndexBook(book_uuid=BOOK, channel_id=CHANNEL, index_base=index_url,
                         archiver=archiver, get_json=get_json)
        which = "REAL mnembyte.index_book.IndexBook"
    else:
        book = _EquivalentIndexBook(book_uuid=BOOK, channel_id=CHANNEL, index_base=index_url,
                                    archiver=archiver, get_json=get_json)
        which = "equivalent IndexBook client"
    print(f"\n[contract-match] exercising: {which}")

    # /root_kuri
    assert book.root_kuri == store.get_root(BOOK)
    # list a folder
    children = book.list("/posts/2024-01")
    labels = {c.label if hasattr(c, "label") else c["path"] for c in children}
    assert "/posts/2024-01/first.txt" in labels
    # resolve + retrieve a leaf by kuri (the real read path). `key` is required by IndexBook's signature
    # but inert here — the tool is chain-free/keyless, so nothing is sealed and the bytes come back as-is.
    dummy_key = b"\x00" * 32
    assert book.retrieve("/posts/2024-01/first.txt", key=dummy_key) == LEAVES["/posts/2024-01/first.txt"]
    assert book.retrieve("/readme.txt", key=dummy_key) == LEAVES["/readme.txt"]
