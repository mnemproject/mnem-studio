# mnem-localdev

A portable, **chain-free local dev stack** that serves a mnem app's databook (yObjs + bytes) straight
off your local filesystem. It stands up the two HTTP surfaces a mnem app's runtime reads:

- an **index** server — the book's `root_kuri` + folder listings (path → kuri navigation), and
- a **media** gateway — content-addressed bytes by kuri,

backed entirely by the filesystem, with **no chain, no cloud, no secrets**. Because the wire contracts
are byte-identical to the production platform, pointing an app at these localhost URLs instead of the
production ones is a **pure URL swap**.

It is grounded in [Yettagam](https://yettagam.net): every folder is a `/ytypes/list/` yObj, leaves live
at LBFS logical paths, content is addressed by a `blake3://<hex>` **kuri**, and the byte layout is the
platform's `files/blake3/<ab>/<cd>/<hash>.ydata`.

---

## Install / requirements

- **Python ≥ 3.8** (standard library only for the server + CLI).
- **One dependency: `blake3`** — the exact content-address hash the mnem platform uses, so a yObj hashes
  to the *same* kuri here as in production. Any machine that runs a mnem app already has it.

```bash
pip install blake3          # the only external dependency
# then either run in place:
python -m mnem_localdev ...
# or install the console script:
pip install -e .            # gives you the `localdev` command
```

---

## Quick start

```bash
# 1. seed a demo databook into a fresh store (or point at your own leaves — see below)
python -m mnem_localdev seed --store ./mystore

# 2. bring the stack up (foreground; Ctrl-C to stop == `down`)
python -m mnem_localdev up --store ./mystore --index-port 8067 --media-port 8066
```

`up` prints exactly what to point your app at:

```
mnem-localdev is up (chain-free, local filesystem).
  store      /abs/path/mystore
  index_url  http://127.0.0.1:8067
  media_url  http://127.0.0.1:8066

Point your app's runtime at these instead of the production URLs (a pure URL swap):
    MNEM_INDEX_URL=http://127.0.0.1:8067
    MNEM_MEDIA_URL=http://127.0.0.1:8066
```

### CLI

```
localdev up     --store <dir> [--index-port N] [--media-port M] [--host H]
localdev seed   --store <dir> [--book <uuid>] [--file LOGICAL=LOCALFILE ...]
localdev anchor --store <dir> --book <uuid> --root <kuri>
localdev ls     --store <dir> [--book <uuid>]
localdev down   # reminder: `up` is foreground — stop it with Ctrl-C
```

`seed` with no `--file` writes a small demo tree; otherwise each `--file /logical/path=./local.bin`
stores that local file at that logical path. Example hosting your own databook:

```bash
localdev seed --store ./mystore --book 8f… \
  --file /readme.md=./README.md \
  --file /posts/2024-01/hello.txt=./hello.txt
```

---

## How a mnem app hosts its databook locally against this

A mnem app's runtime reads its book through two seams (the platform's canonical index read
client, `IndexBook`, speaks exactly this contract):

1. an **index base URL** it calls for `GET /books/{uuid}/root_kuri` and `GET /books/{uuid}/list?folder=`;
2. a **media base URL** it fetches leaf bytes from by kuri.

Point those two at the URLs `localdev up` prints and the app runs entirely locally. Nothing else changes
— same paths, same JSON shapes, same byte routes:

### Index contract (mirrors the platform's index service)

| Method + path | Response |
|---|---|
| `GET /health` | `{"ok": true}` |
| `GET /books/{book}/root_kuri` | `{"book_uuid", "root_kuri"}` — `404 {"detail":"unknown book"}` if unanchored |
| `GET /books/{book}/list?folder=<path>&limit=<n>&cursor=<keyset>` | `{"book_uuid","folder","next_cursor","entries":[{"path","kuri","is_folder"}]}` — path-keyset paginated |
| `POST /books/{book}/root_kuri` `{"root_kuri": ...}` | the **chain-free anchor**: sets the local root pointer, returns `{"book_uuid","root_kuri"}` |

### Media contract (mirrors the platform's media/byte gateway)

| Method + path | Behaviour |
|---|---|
| `GET /health` | `{"ok": true}` |
| `GET /<book>/objects/<kuri>` | raw bytes for a literal kuri (`blake3://<hex>`), passed through verbatim |
| `GET /<book>/objects/<logical/path>` | resolve the logical path through the tree, serve the leaf bytes |
| `GET /<book>/files/blake3/<ab>/<cd>/<hash>.ydata` | raw bytes; the kuri is spelled in the path (no resolve) |
| `POST /<book>/objects` (body = bytes) | store the bytes, return the derived kuri as `text/plain` |

The `POST /<book>/objects` accept route + the `POST /books/{book}/root_kuri` anchor seam together are the
whole write path: **push bytes → push the folder `/ytypes/list/` yObjs → anchor the new root**. No chain.

---

## Model

### FS store = media (the GCS replacement)

Bytes are content-addressed on disk, mirroring the platform's local-dir storage backend:

```
<store>/<book>/blake3/<ab>/<cd>/<hash>
```

`<ab>/<cd>` are the first four hex chars of the hash. Writes are write-once (identical bytes → identical
kuri → dedup); reads re-hash and reject a corrupted/substituted blob. This directory is the "media" bytes
— exactly what a cloud object store holds in production, here on your disk.

### Index = a live FS tree-walk (not a chain projection)

The index answers `list`/`root_kuri` by **walking the LBFS tree live** from the book's `root_kuri`: a
folder is a `/ytypes/list/` yObj whose `items[]` embed each child's kuri, so listing a folder is one
blob read and resolving a path is one read per level (O(depth)). No chain, no projection cache — it reads
the filesystem as it is right now.

### The local "anchor" (chain-free write)

`root_kuri` lives in a plain text pointer file `<store>/<book>/ROOT_KURI` — the local analog of the
chain's per-channel custodian metadata. Writing = put yObjs/bytes to the store, then set that pointer
(via the CLI `anchor` or `POST /books/{book}/root_kuri`). The index reads the pointer live, so a new root
is served immediately.

---

## Where the chain-free / local model diverges from the real platform

Honest flags — the **read/write wire contracts are identical**; these are what sits *behind* them:

1. **No sealing / keyless.** Production seals leaf *content* bytes (ciphertext addressed by kuri) and the
   app decrypts with the book's content key; folder `/ytypes/list/` yObjs are plaintext canonical JSON in
   both. This tool stores **everything plaintext** — there are no keys in local dev. The app's read still
   works because its `archiver.retrieve(kuri, channel, key=…)` seam just gets the bytes back as-is; the
   `key` argument is accepted and inert.
2. **No auth gate.** Production fronts reads with a capability the index mints (`POST /authorize`,
   `x-mnem-capability` HMAC) and gates `/list` on the on-chain read ACL. This tool serves everything
   ungated (it's your local disk). An app that *sends* a capability header still works — it's ignored.
3. **Live walk vs. cached projection.** Production's index is a rebuildable cache the chain-watcher
   populates by tree-walking each anchored `root_kuri`; this tool walks the FS live on every request.
   Same answers, simpler — and always fresh (no watcher lag, no `/integrity/status` / `/acl/status`).
4. **The anchor is a local pointer, not a chain CAS.** Production anchors via an on-chain
   compare-and-swap of custodian metadata (`from_kuri → to_kuri`) with GRANDPA finality (a two-head
   serving/finalized model). This tool just overwrites a pointer file — single-head, instant, no
   finality, no revocation window.
5. **Not implemented (unneeded locally):** the `mnem://` canonical identity + `/vcs/<ref>/<path>` kilai
   route, private-bucket signed-URL redirects (`x-mnem-redirect` / `POST /authorize-write`), the `421`
   misdirected-routing contract, and read-as-is `files/native/<rel>` serving. Add them only if a specific
   app flow needs them; the core `objects`/`files`/`list`/`root_kuri` surface is complete.

---

## Tests

```bash
python -m pytest -q                                        # unit + live-server smoke (equivalent client)
MNEM_PLATFORM_SRC=/path/to/platform-src python -m pytest -q   # also drive the REAL platform IndexBook
```

`tests/test_contract.py` covers the store, kuri byte-compatibility, the LBFS tree walk, and pagination.
`tests/test_smoke.py` starts the real servers on live ports, curls the contract over HTTP, does a write
roundtrip, and — when `MNEM_PLATFORM_SRC` points at a local checkout of the platform source — exercises the
actual platform `IndexBook` (`root_kuri` / `list` / `resolve` / `retrieve`) against this server,
proving the read path is a true URL swap. Without that env var it runs an equivalent client that mirrors
IndexBook's exact HTTP calls.
```
