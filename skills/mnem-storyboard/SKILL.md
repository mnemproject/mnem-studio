---
name: mnem-storyboard
expose: operator
description: >-
  mnem-storyboard — the PUBLIC, shareable maker skill for mnembyte: author a
  draft storyboard reel and publish it to your mnembyte timeline, all driven by
  the AGENT from your studio/IDE. Use this WHENEVER the user wants to: make /
  author a reel or storyboard, connect their studio to mnembyte (sign up / sign
  in, create or pick a list, generate a studio API key), pick which list to post
  to, or push a finished storyboard package so it publishes public (with its
  license) on mnembyte.net. The maker authors the storyboard (delegating the
  scene/video authoring to the mnem-draft-project-creator pattern), then this
  skill connects to a mnembyte account + a chosen list via a generated API key
  and pushes the package over the mnembyte broker API — mnembyte does the kilai
  upload + the public publish SERVER-SIDE. Any coding agent that can run a shell
  + ask the user a few questions can drive it. The studio/IDE is the surface;
  there is no web UI here — the published reel lives on mnembyte.
app:
  id: SKL-mnem-storyboard
  name: mnem-storyboard
  kind: skill
  status: planned
  visibility: public
  shareable: true
  home: mnembyte.net
  tagline: make a reel, push it to mnembyte — public, with its license
  what: >-
    Author a storyboard reel in your studio, connect it to your mnembyte account
    and a list with one generated API key, and push. mnembyte assembles the
    package, uploads it (media + index + chain anchor), and publishes it public
    on mnembyte.net carrying the license YOU chose. The agent runs the whole
    flow; you make the human calls — what the reel says, which list, and the
    license.
  for_who: >-
    Makers who want to publish reels from their agent/IDE — no mnembyte web app,
    no password handling, no chain plumbing. A shareable skill anyone can install.
  difficulty:
    setup: ~2 min — sign up (or paste a saved session) + mint one studio API key
    requires: [a mnembyte account (free signup), the mnembyte studio client]
  cost:
    model: free to publish on mnembyte's hosted timeline; storage is mnembyte's
  provocations:
    - Make a 5-scene reel about my weekend and publish it CC-BY.
    - Connect me to mnembyte and show my lists.
    - Push this storyboard to my "travel" list, unlisted.
  platforms: [studio, macos, linux, windows]
---

# mnem-storyboard — author a reel, push it to mnembyte

This is a **public, shareable** maker skill. You (the agent) drive the whole loop from the user's
studio/IDE: **author** a draft storyboard, **connect** it to the user's mnembyte account + a chosen list
with a generated **API key**, and **push** the storyboard package. mnembyte (`mnembyte.net`) does the kilai
upload and the public publish **server-side** — this skill never touches the chain, keys, or media
buckets; it authors locally and calls the broker API.

**Faceless / no web UI.** The studio/IDE is the only surface here. The *published* reel lives on
mnembyte — a public timeline page + a subscription feed. The skill's job stops at a successful `push`.

The mechanical HTTP + credential handling is done by the **mnembyte studio client** bundled in this
repo under `tools/mnembyte-client/` (module `mnembyte.client`); you own the flow and the
human-judgment calls (what the reel says, which list, the license, public vs unlisted).

## 0 · The three-step flow

```
(a) author a draft storyboard   → a package on disk (post metadata + scene artifacts)
(b) connect to mnembyte          → sign up / sign in · create-or-select a list · mint an API key
(c) select the list + push       → mnembyte uploads + publishes it public, with its license
```

## a · Author a draft storyboard (delegate the authoring)

A **storyboard package** is what `POST /push` expects: the reel's **`post.yobj`** (its metadata — caption,
**license**, policy) **+ its artifact leaves** (the scene images). Author it with the
**`mnem-draft-project-creator`** pattern and its **video-maker** (the authoring front-end that composes
scenes into a reel) — that is the AUTHORING step this skill builds on. Reference it for the creative work;
this skill takes over at the package.

Land the authored output as a **directory** the client can read (`StoryboardPackage.from_dir`):

```
myreel/
  storyboard.json        # {"caption": "...", "license": "CC-BY-4.0", "policy": "MP-1", "posted_at": "..."}
  01.png  02.jpg  ...     # the scene artifacts (jpeg/png/webp/gif), in order
```

- `storyboard.json` is the `post.yobj`-shaped metadata sidecar (optional fields; caption + **license** are
  the ones that matter for a public reel).
- The images are the artifact leaves. mnembyte re-derives the on-book folder-package (`post.yobj` + image
  leaves) server-side — you supply the CONTENT, not the on-chain layout.

> **License is a human call, and it's load-bearing.** The license you set travels ON THE REEL (the post
> yObj) and governs how others may reuse it once mnembyte publishes it public. Ask the user. Self-authored
> (agent-made for the user) → theirs to license (e.g. `CC-BY-4.0`); reused source material → only a license
> the source permits. Don't publish someone else's material under a license it doesn't carry.

## b · Connect to mnembyte (the client)

Look for a saved session first (default `~/.mnembyte/state.json`, mode 600). If there isn't one, onboard —
you never handle the user's password; signup mints the session + the API key for you:

```bash
python3 -m mnembyte.client signup --email <email> --username <name> --password '<they type it>'
python3 -m mnembyte.client host-timeline            # provision the hosted timeline (the default 'posts' list)
python3 -m mnembyte.client create-list --name posts # the default list binds to the hosted timeline
python3 -m mnembyte.client api-key --default-list-id <list_id> --label studio   # RAW key shown ONCE, stored
```

Returning user: `login --email <email> --password '<…>'` refreshes the bearer; a stored API key keeps
working (it's durable). To post to a *different* named list, `create-list --name travel` (a named list gets
its own book — no `host-timeline` needed) and either mint a key defaulting to it or pass `--list-id` at push.

Equivalently, from Python (what a studio session does directly):

```python
from mnembyte.client import MnembyteClient, StoryboardPackage
mb = MnembyteClient("https://mnembyte.net", state_path="~/.mnembyte/state.json")
mb.signup(email, username, password)        # or mb.login(email, password)
mb.create_timeline()                        # once, for the default 'posts' list
posts = mb.create_list("posts")
mb.generate_api_key(default_list_id=posts["list_id"])   # holds the key client-side
```

**Two credentials, two jobs** (mirrors the server):
- the **bearer token** — account acts (signup/login/create-list/mint-key); short-lived, re-`login` to refresh.
- the **studio API key** (`mbyte_<id>.<secret>`) — the **push** credential; durable, shown once, revocable
  (`revoke_api_key(key_id)`). Store it as the account's act-as-poster credential; it CANNOT mint/revoke keys
  or change the password (those need a real sign-in). If it leaks, revoke it and mint a fresh one.

## c · Select the list + push

Ask the user which list, and whether **public** (listed) or **unlisted** (reachable by link), then push:

```bash
python3 -m mnembyte.client push --dir ./myreel --caption "the stick" --license CC-BY-4.0
#   --list-id <id>     target a specific list (else the API key's default list)
#   --visibility unlisted
#   --no-publish       land the reel WITHOUT publishing it public (a private draft on the book)
```

Or in Python:

```python
pkg = StoryboardPackage.from_dir("./myreel")     # or build it scene-by-scene with pkg.add_scene(...)
res = mb.push(pkg, list_id=posts["list_id"], publish=True, visibility="public")
```

**What the result means:**
- `landed: true, published: "public"` — the reel is live: `res["public_url"]` is its public timeline page;
  the license rides on the reel. This is the success you're driving toward.
- `landed: true, published: null` — landed on the book but not published (you passed `publish=False`).
- `landed: false, deferred_to_pr: true` — the push **deferred to a custodian pull_request** (the studio held
  no standing actant seat, or the anchor hit a conflict/contention). mnembyte reconciles by MERGE, never a
  clobber; nothing is published yet. Tell the user it's queued for the custodian; `res["pull_request"]`
  identifies it. Don't retry in a loop — a deferral is a governance outcome, not a transient error.

## Boundaries

- **Public + shareable.** This skill is meant to be installed and shared. It authors locally and calls the
  public mnembyte broker API — no custodian/infra/chain powers, nothing machine-specific.
- **The agent never handles the user's password beyond the one signup/login call they type into.** No
  password is stored; only the bearer token + the studio API key are held (client-side, mode 600).
- **Publishing is public.** A `publish=True` push puts the reel on mnembyte's public timeline with the
  license you set — confirm the caption, list, license, and visibility with the user before pushing. To
  un-publish after the fact is a mnembyte custodian action, not something this skill can undo.
- **No chain / kilai / media logic here.** mnembyte does the remote-actant upload + the public-mirror
  publish server-side; this skill is purely the maker/IDE front-end over the API.

## Follow-on (not yet wired)

- **Authoring assets** — the `mnem-draft-project-creator` video-maker + the `storyboard` yType (the reel's
  fixed player shell + declarative timeline) are bundled in this repo under `skills/mnem-draft-project-creator/`.
  This skill REFERENCES that authoring step and defines the package shape (`storyboard.json` + scene
  artifacts) the client pushes; wiring the video-maker output directly into `from_dir` is the follow-on.
- **Live push** — the client + skill are exercised against the mnembyte API over a fake-chain test
  harness (unit-tested). The real fork / channels / gateway + chain anchor + key mint + `mnembyte.net`
  deploy are the operator/custodian gate (the platform's live cutover).
