#!/usr/bin/env python3
"""draftbook — the local yettagam software designer / draft-project archivist.

Turns uploaded artifacts (files, links) into a LOCAL draftbook that mirrors a
remote mnem archive: raw uploads land in `inbox/`, source bytes are preserved
under `files/`, and a schema-valid yObj per artifact is generated under
`objects/` — every yObj carrying its LICENSE and a provenance graph edge. A
book-local `storyboard` yType composes the preserved artifacts into a short.

Faithful to the mnem skills stack (consulted, not reinvented):
  - yettagam-software-design: yTypes/yObjs, LBFS layout (inbox/files/objects,
    week-shard W<n> + first-4 of the parent-path hash), logical-paths-ONLY inside
    yObjs, reuse standard types (image/video/document/url) first, book-local
    custom types under `.ytypes/`.
  - mnem-accession: preserve the source, carry provenance into every yObj via a
    graph edge (axiom:derivedFrom), license is first-class.
  - closed predicate vocabulary (content/predicates.py): part:hasComponent,
    axiom:derivedFrom, rel:references.

Stdlib only, so it is portable. Notes where a real archive differs:
  - kuri/fixity uses blake3 in mnem; here we use blake2b as a stand-in and leave
    root_kuri to be derived on publish (fork -> stage -> publish).
  - dimension/duration extraction that needs codecs is left pending: such an
    artifact is filed as a schema-valid `/ytypes/base/` placeholder (the
    documented "extraction pending" behavior), upgraded when metadata lands.

Usage:
  draftbook.py init <book> [--root DIR]
  draftbook.py add  <book> --file PATH --license SPDX [--title T] [--source-medium LABEL]
  draftbook.py link <book> --url URL  --license SPDX [--title T] [--snapshot PATH]
  draftbook.py storyboard <book> --from SPEC.json [--name NAME]
  draftbook.py validate <book>
  draftbook.py ls <book>
"""
from __future__ import annotations
import argparse, hashlib, json, mimetypes, os, re, shutil, struct, sys, time, uuid
from pathlib import Path
from datetime import datetime, timezone

DEFAULT_ROOT = "draftbooks"
SKILL_DIR = Path(__file__).resolve().parent.parent          # skill root
STORYBOARD_YTYPE = SKILL_DIR / "ytypes" / "storyboard.ytype"

# physical-leak guards: none of these may appear INSIDE a yObj (logical paths only)
FORBIDDEN = ["gs://", "storage.googleapis.com", "X-Goog-Signature",
             "x-amz-signature", ".r2.cloudflarestorage.com", "://localhost", "file://"]

# ---------------------------------------------------------------- helpers
def now_date() -> str: return datetime.now(timezone.utc).strftime("%Y-%m-%d")
def now_iso() -> str:  return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def week() -> str:     return "W%d" % (int(time.time()) // 604800)   # weeks from unix epoch
def h4(s: str) -> str: return hashlib.blake2b(s.encode(), digest_size=8).hexdigest()[:4]
def hpref(u: str):                                # YFF-4 hash-prefix shard <aa>/<bb>
    d = hashlib.blake2b(u.encode(), digest_size=4).hexdigest(); return d[:2], d[2:4]
MEDIA_LIFETIME = {"tape": "P20Y", "vault": "P10Y"}   # everything else defaults to P5Y (harddisk/ssd/cloud/…)
def slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s[:64] or "untitled"
def kuri(data: bytes) -> str:                               # blake2b stands in for blake3
    return "blake2b://" + hashlib.blake2b(data, digest_size=32).hexdigest()  # real archive: blake3://

def book_dir(root: str, book: str) -> Path: return Path(root) / book
def die(msg: str): print("error: " + msg, file=sys.stderr); sys.exit(1)

def image_dims(b: bytes):
    """PNG/JPEG/SVG dimensions from header bytes, stdlib only. None if unknown."""
    try:
        if b.lstrip()[:1] == b"<":                           # SVG: viewBox or width/height attrs
            head = b[:2048].decode("utf-8", "ignore")
            m = re.search(r'viewBox="[\d.\-]+[ ,]+[\d.\-]+[ ,]+([\d.]+)[ ,]+([\d.]+)"', head)
            if not m:
                m = re.search(r'width="([\d.]+)"\s+height="([\d.]+)"', head)
            return [int(float(m.group(1))), int(float(m.group(2)))] if m else None
        if b[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", b[16:24]); return [w, h]
        if b[:2] == b"\xff\xd8":                             # JPEG: scan SOF markers
            i = 2
            while i < len(b) - 9:
                if b[i] != 0xFF: i += 1; continue
                m = b[i + 1]
                if m in (0xC0, 0xC1, 0xC2, 0xC3):
                    h, w = struct.unpack(">HH", b[i + 5:i + 9]); return [w, h]
                seg = struct.unpack(">H", b[i + 2:i + 4])[0]; i += 2 + seg
    except Exception:
        pass
    return None

def load_manifest(bd: Path) -> dict:
    f = bd / "manifest.json"
    if not f.exists(): die("not a draftbook (no manifest.json): %s" % bd)
    return json.loads(f.read_text())

def write_yobj(bd: Path, rel: str, obj: dict):
    p = bd / rel; p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")

def list_media(bd: Path):
    out = []; sd = bd / "storage"
    if sd.exists():
        for p in sorted(sd.rglob("*.yobj")):
            try: o = json.loads(p.read_text())
            except Exception: continue
            out.append({"path": str(p.relative_to(bd)), "label": o.get("label", ""),
                        "name": o.get("name", ""), "uuid": p.stem})
    return out

def resolve_source(bd: Path, spec):
    """Accession rule: the source medium must be registered BEFORE the first byte."""
    media = list_media(bd)
    if spec:
        for m in media:
            if spec in (m["uuid"], m["name"], m["label"], m["path"]) or slug(spec) == m["name"]:
                return m["path"]
        die("no source medium matches %r — register it first:\n"
            "  draftbook.py source %s --label %r" % (spec, bd.name, spec))
    if len(media) == 1:
        print("  (source medium: %s)" % media[0]["path"]); return media[0]["path"]
    if not media:
        die("register the SOURCE medium before the first byte (accession rule):\n"
            "  python3 scripts/draftbook.py --root <root> source %s --label \"<where it came from>\" "
            "[--kind harddisk|ssd|cloud|folder|device]" % bd.name)
    die("multiple source media — pass --source-medium <label>:\n  " +
        "\n  ".join("%s  (%s)" % (m["label"], m["path"]) for m in media))

# ---------------------------------------------------------------- commands
def cmd_init(a):
    bd = book_dir(a.root, a.book)
    if bd.exists(): die("already exists: %s" % bd)
    for sub in ("inbox", "files", "objects", "objects/storyboard", ".ytypes",
                "storage", "locations"):     # storage/locations = the private inventory tier
        (bd / sub).mkdir(parents=True, exist_ok=True)
    # book-local custom type (consulted from the skill's ytypes/)
    if STORYBOARD_YTYPE.exists():
        shutil.copyfile(STORYBOARD_YTYPE, bd / ".ytypes" / "storyboard.ytype")
    manifest = {
        "book_uuid": str(uuid.uuid4()),
        "name": a.book,
        "label": a.book.replace("-", " ").title(),
        "created_at": now_iso(),
        "yff": "YFF-1",                       # weekly shard (personal-timeline default)
        "root_kuri": "blake2b://pending",     # derived on publish (fork -> stage -> publish)
        "channel_config": {"index": None, "media": None},   # wired at deploy, not hardcoded
        "note": "Local draftbook — mirrors a remote mnem archive's structure. "
                "Port via fork -> stage -> publish; root_kuri is derived then.",
    }
    (bd / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    # root list yObj (LBFS: every folder is a /ytypes/list/)
    write_yobj(bd, "objects/list.yobj", {
        "ytype": "/ytypes/list/", "ytype_label": "List", "name": a.book,
        "label": manifest["label"], "description": "root of the draftbook", "graph": []})
    print("initialized draftbook %s  (book_uuid %s)" % (bd, manifest["book_uuid"]))
    _tree(bd)

def cmd_source(a):
    """Register the SOURCE medium (where artifacts came from) BEFORE any accession."""
    bd = book_dir(a.root, a.book); load_manifest(bd)
    uid = str(uuid.uuid4()); aa, bb = hpref(uid); graph = []
    if a.location:                                   # register WHERE it lives (private tier)
        luid = str(uuid.uuid4()); la, lb = hpref(luid)
        lrel = "locations/%s/%s/%s.yobj" % (la, lb, luid)
        write_yobj(bd, lrel, {"ytype": "/ytypes/base/", "ytype_label": "Location",
            "name": slug(a.location), "label": a.location,
            "description": "a location (private tier)", "graph": [],
            "extra_metadata": {"kind": "location"}})
        graph.append({"predicate": "spatial:within", "yobj": lrel})
    rec = {"ytype": "/ytypes/storage/", "ytype_label": "Storage",
           "name": slug(a.label), "label": a.label,
           "description": "source medium — where accessioned artifacts came from (private tier; "
                          "no fixity_anchor: it is the SOURCE, not the store holding the book)",
           "storage_kind": a.kind, "status": "online",
           "accessioned_at": now_date(), "last_verified": now_date(),
           "media_lifetime": MEDIA_LIFETIME.get(a.kind, "P5Y"), "graph": graph}
    if a.serial: rec["serial_number"] = a.serial
    if a.capacity: rec["extra_metadata"] = {"capacity": a.capacity}
    rel = "storage/%s/%s/%s.yobj" % (aa, bb, uid)
    write_yobj(bd, rel, rec)
    print("registered source medium %r  (accession no. %s)" % (a.label, uid))
    print("  yObj -> %s  kind %s%s" % (rel, a.kind, "  + location" if a.location else ""))
    print("  now accession derives from it:  add/link … --source-medium %r" % a.label)

def _accession_common(ytype, name, label, desc, license_, src_logical, native_path, extra):
    obj = {
        "ytype": ytype, "ytype_label": ytype.strip("/").split("/")[-1].title(),
        "name": name, "label": label, "description": desc,
        "license": license_,                                # first-class (accession)
        "provenance": {"native_path": native_path, "retrieved_at": now_date(),
                       "accessioned_by": "mnem-draft-project-creator"},
        "graph": [],
    }
    if src_logical:
        obj["provenance"]["source_medium"] = src_logical
        obj["graph"].append({"predicate": "axiom:derivedFrom", "yobj": src_logical})
    if extra:
        obj["extra_metadata"] = extra
    return obj

def cmd_add(a):
    bd = book_dir(a.root, a.book); load_manifest(bd)
    src = Path(a.file)
    if not src.is_file(): die("no such file: %s" % src)
    data = src.read_bytes()                              # read the source ONCE; write both copies from memory
    w = week(); ext = src.suffix.lstrip(".").lower() or "bin"
    parent = "files/%s" % w; hh = h4(parent)
    fname = "%s.%s" % (slug(a.title or src.stem), ext)
    # 1) land raw in inbox, then organize into files/ (preserve the bytes)
    inbox_rel = "inbox/%s/%s" % (w, fname)
    (bd / inbox_rel).parent.mkdir(parents=True, exist_ok=True); (bd / inbox_rel).write_bytes(data)
    files_rel = "%s/%s/%s" % (parent, hh, fname)
    (bd / files_rel).parent.mkdir(parents=True, exist_ok=True); (bd / files_rel).write_bytes(data)
    # 2) choose a standard yType; extraction-pending -> base placeholder (documented)
    mime = (mimetypes.guess_type(fname)[0] or "").split("/")[0]
    dims = image_dims(data) if mime == "image" else None
    title = a.title or src.stem
    srcmed = resolve_source(bd, a.source_medium)          # accession rule: source registered first
    common = dict(name=slug(title), label=title,
                  desc="%s artifact preserved into the draftbook" % (mime or "binary"),
                  license_=a.license, src_logical=srcmed,
                  native_path=str(src), extra={"source": files_rel, "kuri": kuri(data)})
    if mime == "image" and dims:
        obj = _accession_common("/ytypes/image/", **common)
        obj["dimensions"] = {"width": dims[0], "height": dims[1]}
        obj["thumbnail"] = files_rel            # draft: thumbnail == source (real: generated)
    elif mime == "document" or ext in ("txt", "md", "html", "pdf", "json"):
        obj = _accession_common("/ytypes/document/", **common)
    else:
        # video/audio/unknown — needs codec extraction -> schema-valid base placeholder
        obj = _accession_common("/ytypes/base/", **common)
        obj["extra_metadata"]["extraction"] = "pending"
        obj["extra_metadata"]["intended_ytype"] = "/ytypes/%s/" % (mime or "media")
    obj_rel = "objects/%s/%s/%s.yobj" % (w, hh, fname)
    write_yobj(bd, obj_rel, obj)
    print("accessioned %s\n  bytes  -> %s\n  yObj   -> %s\n  ytype  %s  license %s"
          % (src.name, files_rel, obj_rel, obj["ytype"], a.license))
    print("  logical path (use in a storyboard 'shows'): %s" % obj_rel)

def cmd_link(a):
    bd = book_dir(a.root, a.book); load_manifest(bd)
    w = week(); parent = "files/%s" % w; hh = h4(parent)
    title = a.title or a.url
    name = slug(title)
    extra = {}
    if a.snapshot:                                          # preserve a local snapshot of the target
        snap = Path(a.snapshot)
        if not snap.is_file(): die("no such snapshot file: %s" % snap)
        sdata = snap.read_bytes()
        srel = "%s/%s/%s.%s" % (parent, hh, name, snap.suffix.lstrip(".") or "html")
        (bd / srel).parent.mkdir(parents=True, exist_ok=True); (bd / srel).write_bytes(sdata)
        extra["snapshot"] = srel; extra["kuri"] = kuri(sdata)
    srcmed = resolve_source(bd, a.source_medium)            # accession rule: source registered first
    obj = {
        "ytype": "/ytypes/url/", "ytype_label": "Url", "name": name, "label": title,
        "description": "archived web link",
        "url": a.url,                                       # the archived SUBJECT url (allowed on url types)
        "license": a.license,
        "provenance": {"source_url": a.url, "source_medium": srcmed, "retrieved_at": now_date(),
                       "accessioned_by": "mnem-draft-project-creator"},
        "graph": [{"predicate": "axiom:derivedFrom", "yobj": srcmed}], "extra_metadata": extra,
    }
    obj_rel = "objects/%s/%s/%s.url.yobj" % (w, hh, name)
    write_yobj(bd, obj_rel, obj)
    print("archived link %s\n  yObj -> %s  license %s" % (a.url, obj_rel, a.license))
    print("  logical path (use in a storyboard 'shows'): %s" % obj_rel)

def cmd_storyboard(a):
    bd = book_dir(a.root, a.book); load_manifest(bd)
    spec = json.loads(Path(a.from_).read_text())
    name = slug(a.name or spec.get("title") or "storyboard")
    kfs = spec.get("keyframes", [])
    # collect artifact references from keyframes AND puppets -> graph edges (part:hasComponent)
    shown = []
    for k in kfs:
        for s in k.get("shows", []):
            if s not in shown: shown.append(s)
    for p in spec.get("puppets", []):
        a = p.get("artifact")
        if a and a not in shown: shown.append(a)
    obj = {
        "ytype": "/.ytypes/storyboard/", "ytype_label": "Storyboard",
        "name": name, "label": spec.get("title", name),
        "description": spec.get("subtitle", "a keyframe storyboard"),
        "duration": spec.get("duration", 10.0),
        "aspect": spec.get("aspect", "9:16"),
        "render": spec.get("render", "composite"),
        "style": spec.get("style", ""),
        "keyframes": kfs,
        "graph": [{"predicate": "part:hasComponent", "yobj": s} for s in shown],
    }
    for k in ("puppets", "music", "shakes", "camera", "publish", "palette"):    # puppet-show layers pass through verbatim
        if k in spec: obj[k] = spec[k]
    obj_rel = "objects/storyboard/%s.yobj" % name
    write_yobj(bd, obj_rel, obj)
    print("wrote storyboard yObj -> %s  (%d keyframes, %d artifacts referenced)"
          % (obj_rel, len(kfs), len(shown)))

def cmd_validate(a):
    bd = book_dir(a.root, a.book); load_manifest(bd)
    objs = sorted(set((bd / "objects").rglob("*.yobj")) |
                  set((bd / "storage").rglob("*.yobj")) |     # private tier: source media
                  set((bd / "locations").rglob("*.yobj")))    # private tier: locations
    ok = 0; problems = []
    existing = {str(p.relative_to(bd)) for p in objs}
    for p in objs:
        rel = str(p.relative_to(bd))
        try:
            raw = p.read_text(); obj = json.loads(raw)
        except Exception as e:
            problems.append("%s: not valid JSON (%s)" % (rel, e)); continue
        errs = []
        if not str(obj.get("ytype", "")).startswith("/"):
            errs.append("ytype missing or not a logical path")
        if not obj.get("name") or not obj.get("label"):
            errs.append("missing name/label")
        low = raw.lower()
        for bad in FORBIDDEN:
            if bad.lower() in low:
                errs.append("physical leak (logical-paths-only): %r" % bad)
        # graph edges + keyframe 'shows' must resolve to existing objects
        for e in obj.get("graph", []):
            tgt = e.get("yobj", "")
            if tgt and tgt not in existing and not tgt.startswith("/ytypes/") and tgt.endswith(".yobj"):
                errs.append("graph edge -> missing object %s" % tgt)
        for k in obj.get("keyframes", []):
            for s in k.get("shows", []):
                if s not in existing:
                    errs.append("keyframe shows -> missing object %s" % s)
        if errs: problems.append(rel + ": " + "; ".join(errs))
        else: ok += 1
    print("validated %d yObjs in %s" % (len(objs), bd))
    print("  ok: %d" % ok)
    if problems:
        print("  problems: %d" % len(problems))
        for pr in problems: print("    - " + pr)
        sys.exit(1)
    print("  logical-paths-only: clean · all references resolve")

def _tree(bd: Path, limit=200):
    base = bd
    n = 0
    for p in sorted(bd.rglob("*")):
        if p.is_dir(): continue
        print("  " + str(p.relative_to(base)))
        n += 1
        if n >= limit: print("  …"); break

def cmd_ls(a):
    bd = book_dir(a.root, a.book); m = load_manifest(bd)
    print("draftbook %s  (book_uuid %s, %s)" % (bd, m["book_uuid"], m["yff"]))
    _tree(bd)

# ---------------------------------------------------------------- cli
def main():
    ap = argparse.ArgumentParser(prog="draftbook", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=DEFAULT_ROOT, help="draftbooks root (default: ./draftbooks)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("book"); p.set_defaults(fn=cmd_init)
    p = sub.add_parser("source"); p.add_argument("book"); p.add_argument("--label", required=True)
    p.add_argument("--kind", default="harddisk",
                   choices=["harddisk", "ssd", "cloud", "web", "folder", "device", "tape", "vault", "upload"])
    p.add_argument("--serial"); p.add_argument("--location"); p.add_argument("--capacity")
    p.set_defaults(fn=cmd_source)
    p = sub.add_parser("add"); p.add_argument("book"); p.add_argument("--file", required=True)
    p.add_argument("--license", required=True); p.add_argument("--title")
    p.add_argument("--source-medium", dest="source_medium"); p.set_defaults(fn=cmd_add)
    p = sub.add_parser("link"); p.add_argument("book"); p.add_argument("--url", required=True)
    p.add_argument("--license", required=True); p.add_argument("--title")
    p.add_argument("--snapshot"); p.add_argument("--source-medium", dest="source_medium")
    p.set_defaults(fn=cmd_link)
    p = sub.add_parser("storyboard"); p.add_argument("book")
    p.add_argument("--from", dest="from_", required=True); p.add_argument("--name")
    p.set_defaults(fn=cmd_storyboard)
    p = sub.add_parser("validate"); p.add_argument("book"); p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("ls"); p.add_argument("book"); p.set_defaults(fn=cmd_ls)
    # --root is a top-level flag — pass it BEFORE the subcommand
    a = ap.parse_args()
    a.fn(a)

if __name__ == "__main__":
    main()
