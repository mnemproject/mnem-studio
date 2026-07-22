"""``localdev`` — the orchestrator CLI.

    localdev up    --store <dir> [--index-port N] [--media-port M] [--host H]
    localdev anchor --store <dir> --book <uuid> --root <kuri>
    localdev seed   --store <dir> --book <uuid> [--demo | --file PATH=LOCALFILE ...]
    localdev ls     --store <dir> [--book <uuid>]
    localdev down                     (prints how to stop; `up` runs in the foreground)

``up`` creates the store dir if absent, starts BOTH servers, and prints the ``index_url`` + ``media_url``
to point an app at. It runs in the foreground; Ctrl-C stops both (that is ``down``).
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

from .seed import build_tree
from .server import (DEFAULT_INDEX_PORT, DEFAULT_MEDIA_PORT,
                     make_index_server, make_media_server, serve_in_thread)
from .store import ContentError, FsStore

_DEMO_BOOK = "00000000-0000-4000-8000-000000000001"
_DEMO_LEAVES = {
    "/readme.txt": b"hello from mnem-localdev\n",
    "/posts/2024-01/first.txt": b"first post body\n",
    "/posts/2024-01/second.txt": b"second post body\n",
    "/media/logo.txt": b"(pretend this is an image blob)\n",
}


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def cmd_up(args) -> int:
    store_dir = Path(args.store).expanduser().resolve()
    store_dir.mkdir(parents=True, exist_ok=True)
    store = FsStore(store_dir)

    index = make_index_server(store, args.index_port, host=args.host, access_log=_log)
    media = make_media_server(store, args.media_port, host=args.host, access_log=_log)
    serve_in_thread(index)
    serve_in_thread(media)

    index_url = f"http://{args.host}:{args.index_port}"
    media_url = f"http://{args.host}:{args.media_port}"
    print("mnem-localdev is up (chain-free, local filesystem).")
    print(f"  store      {store_dir}")
    print(f"  index_url  {index_url}")
    print(f"  media_url  {media_url}")
    print()
    print("Point your app's runtime at these instead of the production URLs (a pure URL swap):")
    print(f"    MNEM_INDEX_URL={index_url}")
    print(f"    MNEM_MEDIA_URL={media_url}")
    print()
    books = store.books()
    print(f"  books in store: {books or '(none yet — run `localdev seed`)'}")
    print("Press Ctrl-C to stop (this is `down`).")

    stop = {"flag": False}

    def _handle(_sig, _frm):
        stop["flag"] = True
    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)
    try:
        while not stop["flag"]:
            time.sleep(0.25)
    finally:
        index.shutdown()
        media.shutdown()
        print("\nmnem-localdev stopped.")
    return 0


def cmd_anchor(args) -> int:
    store = FsStore(Path(args.store).expanduser().resolve())
    try:
        store.set_root(args.book, args.root)
    except ContentError as exc:
        _log(f"anchor failed: {exc}")
        return 1
    print(f"anchored {args.book} -> {args.root}")
    return 0


def cmd_seed(args) -> int:
    store_dir = Path(args.store).expanduser().resolve()
    store_dir.mkdir(parents=True, exist_ok=True)
    store = FsStore(store_dir)
    book = args.book or _DEMO_BOOK
    if args.file:
        leaves = {}
        for spec in args.file:
            logical, _, local = spec.partition("=")
            if not local:
                _log(f"bad --file spec {spec!r} (want LOGICAL=LOCALPATH)")
                return 1
            leaves[logical] = Path(local).expanduser().read_bytes()
    else:
        leaves = dict(_DEMO_LEAVES)
    root = build_tree(store, book, leaves)
    print(f"seeded book {book} with {len(leaves)} leaf/leaves")
    print(f"  root_kuri {root}")
    for lp in sorted(leaves):
        print(f"    {lp}")
    return 0


def cmd_ls(args) -> int:
    store = FsStore(Path(args.store).expanduser().resolve())
    from . import tree
    books = [args.book] if args.book else store.books()
    if not books:
        print("(no books in store)")
        return 0
    for book in books:
        root = store.get_root(book)
        print(f"book {book}")
        print(f"  root_kuri {root or '(unanchored)'}")
        if root:
            for lp, k in tree.walk_leaves(store, book, root):
                print(f"    {lp}  {k}")
    return 0


def cmd_down(_args) -> int:
    print("`localdev up` runs in the foreground; stop it with Ctrl-C in its terminal (that is `down`).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="localdev", description="chain-free local dev stack for mnem databooks")
    sub = p.add_subparsers(dest="cmd", required=True)

    up = sub.add_parser("up", help="start the index + media servers (foreground)")
    up.add_argument("--store", required=True, help="the FS store directory (created if absent)")
    up.add_argument("--index-port", type=int, default=DEFAULT_INDEX_PORT)
    up.add_argument("--media-port", type=int, default=DEFAULT_MEDIA_PORT)
    up.add_argument("--host", default="127.0.0.1")
    up.set_defaults(func=cmd_up)

    an = sub.add_parser("anchor", help="set a book's local root pointer (the chain-free anchor)")
    an.add_argument("--store", required=True)
    an.add_argument("--book", required=True)
    an.add_argument("--root", required=True, help="the root_kuri (blake3://<hex>) to anchor")
    an.set_defaults(func=cmd_anchor)

    sd = sub.add_parser("seed", help="seed an LBFS tree (demo, or LOGICAL=LOCALFILE leaves)")
    sd.add_argument("--store", required=True)
    sd.add_argument("--book", help=f"book uuid (default {_DEMO_BOOK})")
    sd.add_argument("--file", action="append", metavar="LOGICAL=LOCALFILE",
                    help="a leaf: logical path = local file to read (repeatable)")
    sd.set_defaults(func=cmd_seed)

    ls = sub.add_parser("ls", help="list books / a book's leaves")
    ls.add_argument("--store", required=True)
    ls.add_argument("--book")
    ls.set_defaults(func=cmd_ls)

    dn = sub.add_parser("down", help="how to stop a running `up`")
    dn.set_defaults(func=cmd_down)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
