# mnem-studio

**Make a reel in your studio, publish it to your timeline.** `mnem-studio` is the desktop/IDE side of
[mnembyte](https://mnembyte.net) — a small CLI-and-skills toolkit that any coding agent (or a person at a
shell) can drive to author a storyboard reel locally and push it to a public mnembyte timeline. There is no
web app to log into for authoring: your studio/IDE is the surface, and the published reel lives on
mnembyte.

It is distributed as **skills** — self-contained folders you drop into `.claude/skills/` (or any
agent-skill directory) — plus a couple of **bundled tools** the skills shell out to.

```
mnem-studio/
├── README.md                         ← you are here (the distribution model)
├── skills/
│   ├── mnem-storyboard/              the PUBLIC maker skill: author → connect → push to mnembyte
│   └── mnem-draft-project-creator/   the local "maker front": accession artifacts + compose + video-maker
└── tools/
    ├── mnembyte-client/              the thin HTTP push client (mnembyte.client) the storyboard skill uses
    └── localdev/                     mnem-localdev: a chain-free local FS+index stack to host a book locally
```

---

## The skill-distribution model

This is the one place the model is written down. The pieces fit together like this:

### 1 · yettagam is the base yType system

Everything is data described by a **yType** (a schema) and stored as a **yObj** (a schema-valid object) in a
**book** (a content tree over a logical filesystem, LBFS). [yettagam.net](https://yettagam.net) is the open,
versioned registry of the *standard* yTypes (`/ytypes/base/`, `/ytypes/image/`, `/ytypes/list/`, …). Reuse a
standard type first; declare a book-local custom type only for a genuinely new shape.

### 2 · the storyboard skill programs a post/timeline yType on top of yettagam, and bakes the frontend

`mnem-storyboard` (and its authoring counterpart) declares the reel's yType — a `timeline_post` /
`storyboard` shape that **inherits from `/ytypes/base/`** — and constructs the themed frontend that plays it
back: the fixed player shell (`index.html` / `scene.js` / `player.js`) with the theme and colours baked in.
The reel's metadata (caption, **license**, policy) is the post yObj; the scene images are its artifact
leaves. The yType is the contract; the frontend is generated over it.

### 3 · the draft-project-creator is the maker front

`mnem-draft-project-creator` is where a maker actually *makes* the reel: it **accessions** source artifacts
(images, links, docs) into a local draftbook *with their license and provenance*, composes them into a
keyframed storyboard yObj, and renders/records a short with the bundled **video-maker** (`composite`,
`puppet`, and `seijaku` render modes; captions are multi-language). The draftbook is byte-for-byte the shape
of a remote mnem archive, so graduating a project is a mechanical fork → stage → publish, not a rewrite.

### 4 · localdev hosts the databook locally (chain-free, URL-swap to prod)

`tools/localdev` (`mnem-localdev`) stands up the two HTTP surfaces a mnem app reads — an **index** (root_kuri
+ folder listings) and a **media gateway** (content-addressed bytes by `blake3://` kuri) — backed entirely
by the local filesystem. **No chain, no cloud, no secrets.** The wire contracts are byte-identical to the
production platform, so moving an app from local to production is a **pure URL swap** (`MNEM_INDEX_URL` /
`MNEM_MEDIA_URL`). It's how you develop and preview a book before it ever touches a real deployment.

### 5 · skills self-update from mnembyte.net

Each skill can refresh its own `SKILL.md` and bundled tools from **`mnembyte.net/skills/<name>`**,
content-addressed — so an installed studio pulls the latest maker logic without you re-cloning this repo.
(The published artifacts are addressed by content hash, so an update is verifiable and idempotent.)

### 6 · the per-user timeline shape (treebook layout)

A published timeline is a per-user **treebook** whose posts shard by time:

```
/<YYYY>/W<unix-week>/<YYYY-MM-DD-HH-MM>/<post-uuid>.yobj
/<YYYY>/W<unix-week>/<YYYY-MM-DD-HH-MM>/<post-uuid>/artifacts/…
```

`W<unix-week>` = weeks since the unix epoch (the same YFF-1 week-shard the draftbook uses locally). Each post
is a `<post-uuid>.yobj` (the metadata) alongside a `<post-uuid>/artifacts/` folder holding its scene leaves.
No path ever holds more than a shard's worth of entries.

---

## The flow, end to end

```
author (draft-project-creator + video-maker)   → a package on disk: post.yobj-shaped metadata + scene leaves
   │                                              (preview locally against tools/localdev if you like)
   ▼
connect (mnem-storyboard + tools/mnembyte-client) → sign up / sign in · pick a list · mint one studio API key
   ▼
push (mnem-storyboard)                          → mnembyte uploads + publishes it PUBLIC, carrying its license
```

The studio never touches the chain, keys, or media buckets: the `push` calls the mnembyte broker API and
mnembyte does the upload + public publish server-side.

---

## Dependency: yettagam (referenced, not vendored)

`mnem-studio` **depends on yettagam** but does not vendor the whole yettagam stack — the storyboard yType is
declared *against* it:

- **The schema registry** is public at **[yettagam.net](https://yettagam.net)** — the standard yTypes and the
  meta-schema (`https://yettagam.net/ytype/1.0.0/schema.json`) the bundled `ytypes/storyboard.ytype` inherits
  from (`/ytypes/base/`). No install needed to *read* a type; it's a public URL.
- **The design toolkit** is the `yettagam-software-design` skill (part of the mnem skills stack). Obtain it
  from the same distribution channel as this bundle (`mnembyte.net/skills/yettagam-software-design`) if you
  need to author or validate new yTypes. `mnem-draft-project-creator` only *consults* it by name for the
  conventions it already applies (logical-paths-only, reuse-standard-types-first, week-shard folders) — you
  can run the maker flow without it.

The bundled tools are stdlib-only (plus `blake3` for `tools/localdev`), so nothing here requires the private
mnem platform repos to run locally.

---

## Requirements

- **Python ≥ 3.8** (stdlib only for the skills' scripts + the mnembyte client).
- **`blake3`** — only for `tools/localdev` (`pip install blake3`); it's the platform's content-address hash,
  so a yObj hashes to the same kuri locally as in production.
- **`ffmpeg`** — optional, for converting a recorded WebM take to MP4 (`tools/../scripts/serve.py`).

## License

This bundle is dual-licensed so code and creative assets travel under the right terms:

- **Code** — the `.py` sources, `pyproject.toml`, and `.ytype` schema declarations are **MIT**
  (see [`LICENSE`](LICENSE)).
- **Content** — the example draftbooks/storyboards, scene artifacts, theme assets, and documentation
  prose are **CC-BY-4.0** (see [`LICENSE-CONTENT.md`](LICENSE-CONTENT.md)).

Each skill folder is independently portable and carries the same terms.
