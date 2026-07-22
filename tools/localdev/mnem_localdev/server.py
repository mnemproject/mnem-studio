"""The two HTTP servers — INDEX and MEDIA — presenting the SAME contracts the app's runtime calls.

Both are plain ``http.server`` (stdlib only), reading the FS store live. Point an app's ``index_url``
and ``media_url`` at these localhost ports instead of the production URLs and nothing else changes.

INDEX (control-plane read surface — mirrors the platform's index service):
  GET  /health                         -> {"ok": true}
  GET  /books/{book}/root_kuri         -> {"book_uuid", "root_kuri"}         (404 unknown book)
  GET  /books/{book}/list?folder=&limit=&cursor=
                                       -> {"book_uuid","folder","next_cursor","entries":[{path,kuri,is_folder}]}
  POST /books/{book}/root_kuri  {"root_kuri": ...}  -> {"book_uuid","root_kuri"}
                                       the CHAIN-FREE ANCHOR seam: set the local root pointer (no chain).

MEDIA (byte gateway — mirrors the platform's media/byte gateway):
  GET  /health                         -> {"ok": true}
  GET  /{book}/objects/{kuri-or-path}  -> raw bytes   (kuri passed through; logical path resolved via tree)
  GET  /{book}/files/blake3/<ab>/<cd>/<hash>.ydata -> raw bytes (kuri spelled in the path; no resolve)
  POST /{book}/objects   body=bytes    -> the derived kuri as text/plain  (the accept/push route)

Chain-free divergences from production are called out in the README; the wire contracts are identical.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

from .kuri import is_files_path, is_kuri, kuri_from_files_path
from .store import ContentError, FsStore
from . import tree

DEFAULT_INDEX_PORT = 8067
DEFAULT_MEDIA_PORT = 8066
DEFAULT_CHILDREN_LIMIT = 500
MAX_CHILDREN_LIMIT = 2000


# --------------------------------------------------------------------------
# Resolver — the FS-backed projection the index handlers read
# --------------------------------------------------------------------------
class FsResolver:
    """Serves the index read contract by walking the FS tree live (no chain, no cache)."""

    def __init__(self, store: FsStore):
        self.store = store

    def root_kuri(self, book_uuid: str) -> Optional[str]:
        return self.store.get_root(book_uuid)

    def children(self, book_uuid: str, folder: str, *, limit: int, after: Optional[str]):
        root = self.store.get_root(book_uuid)
        if root is None:
            raise tree.NotInTree(f"book {book_uuid} has no anchored root")
        return tree.page_children(self.store, book_uuid, root, folder, limit=limit, after=after)


# --------------------------------------------------------------------------
# Shared request-handler helpers
# --------------------------------------------------------------------------
class _JsonHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "mnem-localdev/1.0"

    def log_message(self, fmt, *args):  # keep the console readable; route through the server's logger
        logger = getattr(self.server, "access_log", None)
        if logger:
            logger("%s - %s" % (self.address_string(), fmt % args))

    def _send_json(self, status: int, obj: dict):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, status: int, data: bytes, content_type="application/octet-stream"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_text(self, status: int, text: str):
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(n) if n else b""

    def _split(self) -> Tuple[str, dict]:
        parts = urlsplit(self.path)
        return parts.path, parse_qs(parts.query)


# --------------------------------------------------------------------------
# INDEX handler
# --------------------------------------------------------------------------
class IndexHandler(_JsonHandler):
    @property
    def resolver(self) -> FsResolver:
        return self.server.resolver  # type: ignore[attr-defined]

    def do_GET(self):
        path, qs = self._split()
        if path == "/health":
            return self._send_json(200, {"ok": True})
        segs = [s for s in path.split("/") if s]
        # /books/{book}/root_kuri  |  /books/{book}/list
        if len(segs) == 3 and segs[0] == "books" and segs[2] == "root_kuri":
            book = unquote(segs[1])
            rk = self.resolver.root_kuri(book)
            if rk is None:
                return self._send_json(404, {"detail": "unknown book"})
            return self._send_json(200, {"book_uuid": book, "root_kuri": rk})
        if len(segs) == 3 and segs[0] == "books" and segs[2] == "list":
            book = unquote(segs[1])
            folder = qs.get("folder", ["/"])[0]
            try:
                limit = int(qs.get("limit", [DEFAULT_CHILDREN_LIMIT])[0])
            except ValueError:
                limit = DEFAULT_CHILDREN_LIMIT
            limit = min(max(1, limit), MAX_CHILDREN_LIMIT)
            cursor = qs.get("cursor", [None])[0]
            try:
                entries, next_cursor = self.resolver.children(book, folder, limit=limit, after=cursor)
            except tree.NotInTree:
                # graceful-empty, exactly like the app's IndexBook when a folder isn't projected
                entries, next_cursor = [], None
            return self._send_json(200, {
                "book_uuid": book, "folder": folder,
                "next_cursor": next_cursor, "entries": entries,
            })
        return self._send_json(404, {"detail": "not found"})

    def do_POST(self):
        path, _ = self._split()
        segs = [s for s in path.split("/") if s]
        # POST /books/{book}/root_kuri  -- the chain-free anchor seam
        if len(segs) == 3 and segs[0] == "books" and segs[2] == "root_kuri":
            book = unquote(segs[1])
            try:
                body = json.loads(self._read_body() or b"{}")
                root_kuri = body["root_kuri"]
            except (json.JSONDecodeError, KeyError, TypeError):
                return self._send_json(400, {"detail": "expected JSON {root_kuri}"})
            try:
                self.server.resolver.store.set_root(book, root_kuri)  # type: ignore[attr-defined]
            except ContentError as exc:
                return self._send_json(409, {"detail": str(exc)})
            return self._send_json(200, {"book_uuid": book, "root_kuri": root_kuri})
        return self._send_json(404, {"detail": "not found"})


# --------------------------------------------------------------------------
# MEDIA handler
# --------------------------------------------------------------------------
class MediaHandler(_JsonHandler):
    @property
    def store(self) -> FsStore:
        return self.server.store  # type: ignore[attr-defined]

    def _resolve_to_kuri(self, book: str, path_or_kuri: str) -> str:
        """Mirror the platform media gateway's resolve: a literal kuri passes through; a files/ byte path is
        parsed directly; a logical objects/ path is resolved through the tree from root_kuri."""
        if is_kuri(path_or_kuri):
            return path_or_kuri
        if is_files_path(path_or_kuri):
            return kuri_from_files_path(path_or_kuri)
        root = self.store.get_root(book)
        if root is None:
            raise tree.NotInTree(f"book {book} has no anchored root")
        return tree.resolve(self.store, book, root, path_or_kuri)

    def do_GET(self):
        path, _ = self._split()
        if path == "/health":
            return self._send_json(200, {"ok": True})
        # Parse the RAW path (don't split-and-filter): a kuri carries a literal `://` whose `//` would
        # drop an empty segment, and the platform's gateway client sends the kuri UNENCODED. So slice on
        # the first two separators only: <book>/<kind>/<rest>. `rest` keeps its interior `/` and `://`.
        rel = path.lstrip("/")
        parts = rel.split("/", 2)
        if len(parts) < 3 or parts[1] not in ("objects", "files"):
            return self._send_json(404, {"detail": "not found"})
        book = unquote(parts[0])
        rest = unquote(parts[2])
        if parts[1] == "files":
            target = "files/" + rest             # files/blake3/<ab>/<cd>/<hash>.ydata
        else:
            target = rest                        # a literal kuri (blake3://<hash>) or a logical path
        try:
            kuri = self._resolve_to_kuri(book, target)
            data = self.store.get(book, kuri)
        except (tree.NotInTree, ContentError) as exc:
            return self._send_json(404, {"detail": str(exc)})
        return self._send_bytes(200, data)

    def do_POST(self):
        path, _ = self._split()
        segs = [unquote(s) for s in path.split("/") if s]
        # POST /{book}/objects  -> store bytes, return derived kuri as text/plain
        if len(segs) == 2 and segs[1] == "objects":
            book = segs[0]
            data = self._read_body()
            kuri = self.store.put(book, data)
            return self._send_text(200, kuri)
        return self._send_json(404, {"detail": "not found"})


# --------------------------------------------------------------------------
# Server plumbing
# --------------------------------------------------------------------------
def make_index_server(store: FsStore, port: int, host: str = "127.0.0.1",
                      access_log=None) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), IndexHandler)
    httpd.resolver = FsResolver(store)          # type: ignore[attr-defined]
    httpd.access_log = access_log               # type: ignore[attr-defined]
    return httpd


def make_media_server(store: FsStore, port: int, host: str = "127.0.0.1",
                      access_log=None) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), MediaHandler)
    httpd.store = store                         # type: ignore[attr-defined]
    httpd.access_log = access_log               # type: ignore[attr-defined]
    return httpd


def serve_in_thread(httpd: ThreadingHTTPServer) -> threading.Thread:
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return t
