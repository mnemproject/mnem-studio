---
name: mnem-draft-project-creator
description: >-
  Create a mnem DRAFT PROJECT locally — the portable "yettagam software designer" front door. Use when a
  user wants to upload artifacts (videos, images, links) and turn them into a project: preserve the source
  material with its license, generate a yObj per artifact, compose them into a keyframed short, and keep it
  all in a LOCAL draftbook whose structure MIRRORS a remote mnem archive (inbox/files/objects, LBFS,
  week-shard). Includes the video maker. Portable: drop the folder into any .claude/skills/. Delegates the
  pure schema mechanics to yettagam-software-design, the preservation model to mnem-accession, and the
  application shape to mnem-application-creator — it does not reinvent them.
---

# mnem draft project creator — the local yettagam software designer

Preserve first, compose second. A user brings **artifacts** (uploaded files, links); this skill
**accessions** them into a local **draftbook** that is byte-for-byte the shape of a remote mnem archive,
generates a schema-valid **yObj** for each (carrying its **license** + provenance), and composes them into
a **storyboard** yObj that the bundled **video maker** renders and records as a short. Everything stays on
the local machine and follows the archive structure, so porting later is a mechanical fork → stage →
publish, not a rewrite.

> **The archivist role (why this exists):** preserve source material, save it *with its license*, and build
> the graph over it. The draft is not a scratch folder — it is a real archive-in-miniature. When the project
> graduates, the same book forks into a remote archive unchanged.

## Consult these skills (do not reinvent)
This skill orchestrates; the mechanics live in the broader mnem skills stack:
- **yettagam-software-design** — yTypes/yObjs, the LBFS layout, logical-paths-ONLY, reuse standard types
  first, book-local custom types under `.ytypes/`. (This skill's structure IS that spec, applied.)
- **mnem-accession** — register the source, carry provenance into every yObj, license is first-class.
- **mnem-application-creator** / **mnem-app-data-modeling** — an application is a *skill over a book type*;
  all state is yObjs in a book. This skill is the local, artifact-first entry to that pattern.

## Workflow map (render in chat when this skill triggers)

```
┌─────────────────────────────┐
│ 1 · init the draftbook      │  scaffold inbox/ files/ objects/ .ytypes/ + manifest (book_uuid, YFF-1);
│    (mirror the archive)     │  root_kuri = pending (derived on publish)
└──────────────┬──────────────┘
               ▼
┌─────────────────────────────┐
│ 2 · register the SOURCE     │  the accession rule: register WHERE the bytes came from BEFORE the
│    medium (first!)          │  first accession -> storage/ record (+ optional locations/); private tier
└──────────────┬──────────────┘
               ▼
┌─────────────────────────────┐
│ 3 · accession artifacts     │  files -> preserve bytes (inbox -> files/W<week>/<hash4>/) + a yObj in
│    (preserve + license)     │  objects/… ; reuse standard yType (image/video/document/url); LICENSE required;
│                             │  every yObj gets axiom:derivedFrom -> the source medium
└──────────────┬──────────────┘
               ▼
┌─────────────────────────────┐
│ 3 · compose the storyboard  │  keyframes = the type (SRT/cue-inspired): time window + beat + caption +
│    (keyframes-as-yType)     │  transition + `shows` (logical refs to artifact yObjs) -> part:hasComponent
└──────────────┬──────────────┘
               ▼
┌─────────────────────────────┐
│ 4 · validate                │  logical-paths-only (no gs://, signed URLs); every graph/`shows` resolves
└──────────────┬──────────────┘
               ▼
┌─────────────────────────────┐
│ 5 · make the short          │  video maker reads the storyboard yObj -> animates + records a WebM short
└──────────────┬──────────────┘
               ▼
     port later: fork the book -> stage (index+media) -> publish
```

For a multi-step run render the suite-standard progress panel (✅ ▶️ ⬜ ❌, `[n/m]`, re-render each milestone).

## The draftbook structure (mirrors a remote mnem archive)

```
draftbooks/<book>/
  manifest.json                     book_uuid · YFF-1 (weekly shard) · root_kuri=pending · channel_config
  .ytypes/
    storyboard.ytype                book-local custom type (inherits /ytypes/base/)
  inbox/W<week>/<name.ext>          raw uploads land here first
  files/W<week>/<hash4>/<name.ext>  preserved source bytes (organized)
  objects/
    list.yobj                       LBFS: every folder is a /ytypes/list/
    W<week>/<hash4>/<name.ext>.yobj  one metadata yObj per artifact
    storyboard/<name>.yobj          the storyboard yObj (the short's score)
  storage/<aa>/<bb>/<uuid>.yobj     private tier: the SOURCE media (accession no. = uuid)
  locations/<aa>/<bb>/<uuid>.yobj   private tier: where a medium lives
```
`W<week>` = weeks since the unix epoch; `<hash4>` = first 4 of the parent-path hash. Growing folders shard
by week (YFF-1) — the personal-timeline default. **Logical/physical separation is a hard rule:** yObjs hold
only logical LBFS paths — never `gs://`, signed URLs, or bucket names. (Real archives use blake3 kuris; this
draft stands in with blake2b and derives `root_kuri` at publish.)

## The tool — `scripts/draftbook.py` (stdlib only, portable)

```bash
python3 scripts/draftbook.py --root draftbooks init   <book>
python3 scripts/draftbook.py --root draftbooks source <book> --label NAME [--kind harddisk|ssd|cloud|folder|device] [--serial S] [--location PLACE]
python3 scripts/draftbook.py --root draftbooks add    <book> --file PATH --license SPDX [--title T] [--source-medium LABEL]
python3 scripts/draftbook.py --root draftbooks link   <book> --url URL   --license SPDX [--title T] [--snapshot FILE] [--source-medium LABEL]
python3 scripts/draftbook.py --root draftbooks storyboard <book> --from SPEC.json [--name NAME]
python3 scripts/draftbook.py --root draftbooks validate <book>
python3 scripts/draftbook.py --root draftbooks ls <book>
```

- **`source`** registers WHERE the artifacts came from as a `/ytypes/storage/` record (accession no. =
  uuid) in the private tier, optionally `spatial:within` a `locations/` record. **The accession rule:
  register the source BEFORE the first byte** — `add`/`link` refuse to accession until a source exists
  (if exactly one is registered they use it automatically), and every artifact yObj then carries
  `provenance.source_medium` + an `axiom:derivedFrom` graph edge to it. The chain is complete: artifact
  → source medium → location, not just a bare `native_path`.
- **`add`** reuses a standard yType by mime (`/ytypes/image/` with extracted dimensions, `/ytypes/document/`).
  A type needing codec extraction (video/audio) is filed as a schema-valid `/ytypes/base/` **placeholder**
  with `extraction: pending` — the documented behavior — upgraded when metadata lands.
- **`link`** archives a web URL as `/ytypes/url/` (the subject URL is legitimate metadata on url-types); pass
  `--snapshot FILE` to also preserve a captured copy in `files/`.
- Every yObj carries `license` and `provenance` (`native_path`, `retrieved_at`, optional `source_medium` →
  an `axiom:derivedFrom` graph edge). **License is required on every accession.**
- **`validate`** enforces logical-paths-only and that every `graph` edge and keyframe `shows` resolves.

## The custom yType — `ytypes/storyboard.ytype`

A **book-local** custom type (inherits `/ytypes/base/`). The SRT was the *inspiration*: keyframes ARE the
type — an array of cues, each with `start`/`end`/`beat`/`caption`/`transition` and `shows` (logical refs to
the artifact yObjs it displays). The storyboard yObj mirrors each referenced artifact into its `graph` as a
`part:hasComponent` edge — so **whatever the video uses is preserved and linked** in the same book. `render`
picks the renderer: `composite` (compose the artifacts) or a builtin like `seijaku`.

## The video maker — `assets/video-maker.html`

Self-contained (no dependencies). Point it at a storyboard yObj: `video-maker.html?story=<url-to-.yobj>`.
It reads `duration`, `keyframes`, captions and `render` from the yObj, then:
- **`render: "composite"`** — composites the referenced artifacts (images fetched by their yObj `source`
  path, cover-fit, with the keyframe `transition`) + captions onto a 9:16 canvas.
- **`render: "puppet"`** — **puppetry**: each artifact is an articulable part (a body, an arm that pivots
  at the shoulder, a stick that snaps into halves) moved on keyframed transform tracks
  (`{t,x,y,r,s,o}`, carry-forward props, smoothstep easing), with instancing (one monk artifact seats
  many monks), ambient `sway`, deterministic camera `shakes`, speech-bubble dialogue (per-`voice`
  colors, positioned by the keyframe's `at`), a `camera` track (`{t,x,y,zoom}` push-ins/pull-outs;
  dialogue stays crisp at screen scale), and a `music` score read straight from the yObj (wall-time
  synth events). Captions are **multi-language**: each keyframe may carry an `i18n` map
  (`{ja, zh, ta, es, de, …}`) and the language menu builds itself from whatever keys the yObj holds.
  Artifact discipline: **generate ≤ 20 artifacts; instances are free.**
- **`render: "seijaku"`** — the bundled "SEIJAKU CUT" vector motion language (a worked builtin).

Add **`&view=player`** for the public view: the reel fullscreen on loop (silent until tapped), no
editor chrome, with a hidden menu (scroll up / the ⌄ handle) showing the breadcrumb from the yObj's
`publish` address — `<host>/<user>/<folder>/<name>.yobj` (the mnembyte timeline address). The reel's
closing screen carries a QR of the same address, so the film always knows its way home.
Then **record → short**: `canvas.captureStream()` + a generated Web-Audio score → `MediaRecorder` → a WebM
listed under TAKES with three exits — **⇪ Save** (PUTs the take to `scripts/serve.py`, which writes it
into the draftbook's `inbox/takes/`; the reliable path inside embedded browser panes, which have **no
download manager** — there ↓ Download silently does nothing), **↓ Download** (regular browsers),
**✕ Delete**. Serve with `python3 scripts/serve.py --port 8712 --directory <skill dir>` to enable Save.
(Instagram posting can't be automated from a page; IG wants MP4, browsers export WebM — convert with
ffmpeg before uploading.)

## Worked example — `examples/`
`examples/the-stick.storyboard.json` + the generated `examples/draftbooks/the-stick/` show the whole loop:
an image, a document (the static render), and a source link accessioned with licenses; a 6-keyframe
`SEIJAKU CUT` storyboard yObj referencing them; `validate` green. Regenerate:

```bash
cd skills/mnem-draft-project-creator
python3 scripts/draftbook.py --root examples/draftbooks init   the-stick
python3 scripts/draftbook.py --root examples/draftbooks source the-stick --label "studio disk" --kind harddisk --location "Home studio"
python3 scripts/draftbook.py --root examples/draftbooks add    the-stick --file <img> --license CC-BY-4.0 --title "Blue stroke"
python3 scripts/draftbook.py --root examples/draftbooks link   the-stick --url <src>  --license "fair-use / source"
python3 scripts/draftbook.py --root examples/draftbooks storyboard the-stick --from examples/the-stick.storyboard.json
python3 scripts/draftbook.py --root examples/draftbooks validate the-stick
```

## Portability
The folder is self-contained (SKILL.md + `ytypes/` + `scripts/` stdlib-only + `assets/` + `examples/`). Drop
it into any `.claude/skills/` or the operator bundle. It has no repo dependencies; it only *consults* the
archivist skills by name for the conventions it already applies.

## Checklist
1. Draftbook `init`'d — manifest with `book_uuid`, YFF-1, `root_kuri=pending`, `.ytypes/` seeded?
2. **Source medium registered BEFORE the first accession**; every artifact carries `axiom:derivedFrom` → it?
3. Every artifact accessioned with a **license** and provenance; bytes preserved under `files/`?
4. Standard yType reused where one fits; extraction-pending filed as a `/ytypes/base/` placeholder?
5. Storyboard yObj composes the artifacts (`shows` → `part:hasComponent`)?
6. `validate` green — logical-paths-only, all references resolve?
7. Short renders + records from the storyboard yObj in the video maker?
