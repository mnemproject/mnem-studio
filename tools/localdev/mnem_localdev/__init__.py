"""mnem-localdev — a portable, chain-free LOCAL DEV STACK for serving a mnem app's databook off disk.

It stands up the two HTTP surfaces a mnem app's runtime reads — the INDEX (root_kuri + folder listing)
and the MEDIA gateway (content-addressed bytes by kuri) — backed entirely by the local filesystem, with
NO chain, NO cloud, and NO secrets. Pointing an app at these localhost URLs instead of the production
ones is a pure URL swap: the wire contracts are byte-identical to the platform.

Grounded in YETTAGAM (yObj / /ytypes/list/ folders / LBFS logical paths / blake3 kuris / the
files/blake3/<ab>/<cd>/<hash>.ydata byte layout).
"""
from .kuri import blake3_kuri, canonical_json, yobj_kuri
from .store import ContentError, FsStore
from .server import (DEFAULT_INDEX_PORT, DEFAULT_MEDIA_PORT, FsResolver,
                     make_index_server, make_media_server, serve_in_thread)
from .seed import add_leaf, build_tree

__version__ = "1.0.0"

__all__ = [
    "FsStore", "ContentError", "blake3_kuri", "yobj_kuri", "canonical_json",
    "FsResolver", "make_index_server", "make_media_server", "serve_in_thread",
    "DEFAULT_INDEX_PORT", "DEFAULT_MEDIA_PORT", "build_tree", "add_leaf",
]
