#!/usr/bin/env python3
"""mnembyte studio CLIENT — the AGENT/IDE-held thin HTTP client over the mnembyte broker API.

The maker's studio (the `mnem-storyboard` skill) drives THIS to connect a mnembyte account to a list and
push a storyboard package. It is a thin wrapper over the mnembyte broker API
— NO chain / kilai / index logic lives here: mnembyte does the kilai remote-actant upload SERVER-SIDE (the
`POST /push` endpoint). This client just:

  * `signup(email, username, password)` / `login(email, password)` — hold the opaque bearer token,
  * `create_list(name)` / `lists()` — the per-user named lists (default `posts` binds the hosted timeline),
  * `generate_api_key(...)` — mint the durable, scoped studio key (returned ONCE) and store it client-side,
  * `push(package, list_id=…)` — POST the storyboard package to a chosen list (`POST /push`, API-key authed).

Adapted from loops' agent-held client (`loops_stream.py`): the same `_req`-style HTTP core + a stdlib-only
CLI, generalized to (a) drive EITHER real HTTP (a `urllib` transport, default) OR an injected transport —
so the unit tests drive it against the FastAPI app via `fastapi.testclient.TestClient` with no network — and
(b) hold BOTH credentials mnembyte uses: a short-TTL bearer (account acts: signup/login/create-list/mint-key)
and a durable studio API key (the push credential), persisted client-side like loops persists `~/.loops/key`.

Two credentials, mirroring the server:
  * bearer token   — `Authorization: Bearer <token>`  (account-level acts; short-lived, re-login to refresh)
  * studio API key — `X-Mnembyte-Api-Key: mbyte_<id>.<secret>`  (the push credential; durable, revocable)

Pure stdlib (urllib/json/base64) — no pip install for the real-HTTP path; `httpx`/FastAPI only for the tests.
"""
from __future__ import annotations

import argparse
import base64
import glob
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

DEFAULT_BASE = os.environ.get("MNEMBYTE_BASE", "https://mnembyte.net")
DEFAULT_STATE = os.environ.get("MNEMBYTE_STATE", os.path.expanduser("~/.mnembyte/state.json"))

# Image artifact MIME by extension — a storyboard package's leaves (`from_dir`). Kept tiny + explicit; the
# server re-derives the reel from these photos (`build_storyboard_package`), so this is only the wire hint.
_IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".webp": "image/webp", ".gif": "image/gif"}


class MnembyteError(RuntimeError):
    """A non-2xx API response. `status` is the HTTP code; `detail` the server's `detail` (or raw body)."""

    def __init__(self, status: int, detail: Any):
        self.status = status
        self.detail = detail
        super().__init__(f"{status}: {detail}")


# ---- transport (real urllib by default; a TestClient is injectable for tests) ---------------------------

@dataclass
class _Resp:
    """The minimal response shape the client needs — the same surface `httpx.Response` (TestClient) exposes
    (`status_code`, `.text`, `.json()`), so the urllib transport and a TestClient are interchangeable."""
    status_code: int
    text: str

    def json(self) -> Any:
        return json.loads(self.text) if self.text.strip() else None


class _UrllibTransport:
    """The default real-HTTP transport — stdlib `urllib` only. Its `request(...)` signature MATCHES
    `httpx.Client.request` (method, url, *, headers, json, content), so `MnembyteClient` can be handed a
    `TestClient` instead with zero branching. `url` is a path (`/signup`) joined onto `base`."""

    def __init__(self, base: str, timeout: float = 30.0):
        self.base = base.rstrip("/")
        self.timeout = timeout

    def request(self, method: str, url: str, *, headers: Optional[dict] = None,
                json: Optional[Any] = None, content: Optional[bytes] = None) -> _Resp:
        import json as _json
        data = content if content is not None else (_json.dumps(json).encode() if json is not None else None)
        hdrs = dict(headers or {})
        if json is not None:
            hdrs.setdefault("content-type", "application/json")
        req = urllib.request.Request(self.base + url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return _Resp(r.status, r.read().decode())
        except urllib.error.HTTPError as e:
            return _Resp(e.code, e.read().decode())


# ---- the storyboard package (what the maker authors; what `POST /push` expects) -------------------------

@dataclass
class StoryboardPackage:
    """The storyboard PACKAGE the studio pushes — the `post.yobj` (metadata: caption/license/policy) + its
    artifact leaves (the scene photos). mnembyte assembles the on-book folder-package (`post.yobj` + image
    leaves) SERVER-SIDE from these fields (`build_storyboard_package`); the client only carries them.

    Each photo: `{data: bytes, mime_type: str, label?: str, alt?: str}`. The `license` rides on the reel
    (the post yObj) and governs how others may reuse it once mnembyte publishes it public.
    """
    photos: list = field(default_factory=list)
    caption: Optional[str] = None
    license: Optional[str] = None
    policy: Optional[str] = None
    posted_at: Optional[str] = None

    def add_scene(self, data: bytes, *, mime_type: str, label: Optional[str] = None,
                  alt: Optional[str] = None) -> "StoryboardPackage":
        """Append one artifact leaf (a scene image) to the package."""
        self.photos.append({"data": data, "mime_type": mime_type, "label": label, "alt": alt})
        return self

    @classmethod
    def from_dir(cls, path: str) -> "StoryboardPackage":
        """Load a package authored on disk — the shape the `mnem-draft-project-creator` authoring step
        (bundled in this repo under `skills/mnem-draft-project-creator/`) emits: a folder of scene images
        + an optional `storyboard.json` sidecar (`{caption?, license?, policy?, posted_at?}`).
        Self-contained: no authoring-side code is imported — this reads the emitted FILES, so the client
        + skill are testable without the authoring assets."""
        meta: dict = {}
        for sidecar in ("storyboard.json", "post.json", "package.json"):
            p = os.path.join(path, sidecar)
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    meta = json.load(f)
                break
        pkg = cls(caption=meta.get("caption"), license=meta.get("license"),
                  policy=meta.get("policy"), posted_at=meta.get("posted_at"))
        for f in sorted(glob.glob(os.path.join(path, "*"))):
            ext = os.path.splitext(f)[1].lower()
            if ext in _IMAGE_MIME and os.path.isfile(f):
                with open(f, "rb") as fh:
                    pkg.add_scene(fh.read(), mime_type=_IMAGE_MIME[ext], label=os.path.basename(f))
        if not pkg.photos:
            raise ValueError(f"no scene images ({', '.join(sorted(_IMAGE_MIME))}) found in {path}")
        return pkg

    def _wire_photos(self) -> list:
        """The `photos` array the API expects — each artifact base64-encoded, empty label/alt dropped."""
        out = []
        for p in self.photos:
            item = {"data": base64.b64encode(p["data"]).decode(), "mime_type": p["mime_type"]}
            if p.get("label"):
                item["label"] = p["label"]
            if p.get("alt"):
                item["alt"] = p["alt"]
            out.append(item)
        return out


# ---- the client -----------------------------------------------------------------------------------------

class MnembyteClient:
    """Holds the session (bearer token + studio API key) and drives the broker API. Give it a `base` URL
    for real HTTP, OR an injected `transport` (a `TestClient`) for tests. State (token / api key / account)
    persists to `state_path` when given (mode 600), so a CLI invocation reuses a prior sign-in — mirroring
    how the loops client persists its API key to `~/.loops/key`."""

    def __init__(self, base: str = DEFAULT_BASE, *, transport: Any = None,
                 state_path: Optional[str] = None):
        self.base = base.rstrip("/")
        self._http = transport if transport is not None else _UrllibTransport(self.base)
        self.state_path = state_path
        self.token: Optional[str] = None
        self.account_id: Optional[str] = None
        self.username: Optional[str] = None
        self.api_key: Optional[str] = None
        self.default_list_id: Optional[str] = None
        if state_path and os.path.exists(state_path):
            self._load_state()

    # -- state persistence (client-side; the credential store) --------------------------------------------

    def _load_state(self) -> None:
        with open(self.state_path, encoding="utf-8") as f:
            s = json.load(f)
        self.token = s.get("token")
        self.account_id = s.get("account_id")
        self.username = s.get("username")
        self.api_key = s.get("api_key")
        self.default_list_id = s.get("default_list_id")

    def _save_state(self) -> None:
        if not self.state_path:
            return
        os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
        blob = {"base": self.base, "token": self.token, "account_id": self.account_id,
                "username": self.username, "api_key": self.api_key,
                "default_list_id": self.default_list_id}
        fd = os.open(self.state_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(blob, f)

    # -- HTTP core (loops' `_req`, over the injectable transport) -----------------------------------------

    def _call(self, method: str, path: str, *, body: Optional[dict] = None,
              headers: Optional[dict] = None, auth: str = "bearer") -> Any:
        """One API call → the parsed JSON (raising `MnembyteError` on non-2xx). `auth`: 'bearer' (the account
        token), 'apikey' (the studio push key), or 'none'."""
        hdrs = dict(headers or {})
        if auth == "bearer":
            if not self.token:
                raise MnembyteError(401, "not signed in — call signup() or login() first")
            hdrs["Authorization"] = f"Bearer {self.token}"
        elif auth == "apikey":
            if not self.api_key:
                raise MnembyteError(401, "no studio API key — call generate_api_key() first")
            hdrs["X-Mnembyte-Api-Key"] = self.api_key
        resp = self._http.request(method, path, headers=hdrs, json=body)
        if 200 <= resp.status_code < 300:
            return resp.json()
        try:
            detail = resp.json().get("detail")
        except Exception:  # noqa: BLE001 — a non-JSON error body
            detail = resp.text
        raise MnembyteError(resp.status_code, detail)

    def _require_account(self) -> str:
        if not self.account_id:
            raise MnembyteError(401, "no account — sign in first (signup/login)")
        return self.account_id

    # -- account: signup / login (hold the bearer) --------------------------------------------------------

    def signup(self, email: str, username: str, password: str) -> dict:
        """Register + sign in (M1 step 1). Holds the returned bearer token + account id. The server claims
        the username in the public directory (409 → `MnembyteError` 'username taken')."""
        out = self._call("POST", "/signup", auth="none",
                         body={"email": email, "username": username, "password": password})
        self.token, self.account_id = out["token"], out["account_id"]
        self.username = out.get("username")
        self._save_state()
        return out

    def login(self, email: str, password: str) -> dict:
        """Authenticate → a fresh bearer token (held). Refreshes an expired session without a new signup."""
        out = self._call("POST", "/login", auth="none", body={"email": email, "password": password})
        self.token, self.account_id = out["token"], out["account_id"]
        self.username = out.get("username")
        self._save_state()
        return out

    # -- lists (the per-user named lists; default `posts` = the hosted timeline) --------------------------

    def create_timeline(self) -> dict:
        """Provision + host the member's kodi timeline book (the default `posts` list binds to it). Needed
        once before creating the default list / pushing to it."""
        return self._call("POST", f"/members/{self._require_account()}/timeline")

    def create_list(self, name: str) -> dict:
        """Create a named list (M1.2). `posts` binds the hosted timeline (host it first); any other name
        provisions its own book server-side. Returns the list record (has `list_id`)."""
        out = self._call("POST", f"/members/{self._require_account()}/lists", body={"name": name})
        return out["list"]

    def lists(self) -> list:
        """List the account's lists (public read; no auth needed)."""
        return self._call("GET", f"/members/{self._require_account()}/lists", auth="none")["lists"]

    # -- the studio API key (the durable push credential; stored client-side) -----------------------------

    def generate_api_key(self, *, default_list_id: Optional[str] = None,
                         label: Optional[str] = None) -> dict:
        """Mint a durable, scoped studio API key (M1.2 step 2). The raw key is returned ONCE by the server
        and held client-side (`self.api_key`) as the push credential; the server records only its hash. An
        optional `default_list_id` a bare `push()` targets."""
        body: dict = {}
        if default_list_id is not None:
            body["default_list_id"] = default_list_id
        if label is not None:
            body["label"] = label
        out = self._call("POST", f"/members/{self._require_account()}/api-keys", body=body)
        self.api_key = out["api_key"]
        self.default_list_id = out.get("default_list_id")
        self._save_state()
        return out

    def revoke_api_key(self, key_id: str) -> dict:
        """Revoke a studio key (idempotent). Clears it client-side if it was the held one."""
        out = self._call("POST", f"/members/{self._require_account()}/api-keys/{key_id}/revoke")
        return out

    # -- push a storyboard package (API-key authed; the server does the kilai upload) ---------------------

    def push(self, package: StoryboardPackage, *, list_id: Optional[str] = None,
             publish: bool = True, visibility: Optional[str] = None) -> dict:
        """Push a storyboard package to a chosen list (`POST /push`) — authenticated by the studio API key.
        mnembyte assembles the folder-package and does the kilai remote-actant upload (media + index +
        chain anchor) SERVER-SIDE; on land it publishes the reel public with its license unless
        `publish=False`. `list_id` overrides the key's default list. Returns the push result — either
        `landed=True` (lane 1) or `deferred_to_pr=True` (lane 2: non-actant / conflict → a custodian
        pull_request, never a clobber)."""
        if not package.photos:
            raise MnembyteError(400, "a storyboard package needs at least one scene photo")
        body: dict = {"photos": package._wire_photos(), "publish": publish}
        if list_id is not None:
            body["list_id"] = list_id
        if package.caption is not None:
            body["caption"] = package.caption
        if package.license is not None:
            body["license"] = package.license
        if package.policy is not None:
            body["policy"] = package.policy
        if package.posted_at is not None:
            body["posted_at"] = package.posted_at
        if visibility is not None:
            body["visibility"] = visibility
        return self._call("POST", "/push", body=body, auth="apikey")


# ---- CLI (the studio shells out to this; mirrors the loops skill → loops_stream.py contract) ------------

def _client(a) -> MnembyteClient:
    return MnembyteClient(a.base, state_path=a.state)


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def cmd_signup(a):
    _print(_client(a).signup(a.email, a.username, a.password))


def cmd_login(a):
    _print(_client(a).login(a.email, a.password))


def cmd_host(a):
    _print(_client(a).create_timeline())


def cmd_create_list(a):
    _print(_client(a).create_list(a.name))


def cmd_lists(a):
    _print(_client(a).lists())


def cmd_api_key(a):
    _print(_client(a).generate_api_key(default_list_id=a.default_list_id, label=a.label))


def cmd_push(a):
    c = _client(a)
    pkg = StoryboardPackage.from_dir(a.dir)
    if a.caption is not None:
        pkg.caption = a.caption
    if a.license is not None:
        pkg.license = a.license
    _print(c.push(pkg, list_id=a.list_id, publish=(not a.no_publish), visibility=a.visibility))


def main(argv=None):
    p = argparse.ArgumentParser(prog="mnembyte", description="mnembyte studio client (bearer + API key).")
    p.add_argument("--base", default=DEFAULT_BASE, help="mnembyte origin (default mnembyte.net)")
    p.add_argument("--state", default=DEFAULT_STATE, help="client-side credential store (default ~/.mnembyte)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("signup")
    s.add_argument("--email", required=True)
    s.add_argument("--username", required=True)
    s.add_argument("--password", required=True)
    s.set_defaults(fn=cmd_signup)

    lg = sub.add_parser("login")
    lg.add_argument("--email", required=True)
    lg.add_argument("--password", required=True)
    lg.set_defaults(fn=cmd_login)

    sub.add_parser("host-timeline").set_defaults(fn=cmd_host)

    cl = sub.add_parser("create-list")
    cl.add_argument("--name", required=True)
    cl.set_defaults(fn=cmd_create_list)

    sub.add_parser("lists").set_defaults(fn=cmd_lists)

    ak = sub.add_parser("api-key")
    ak.add_argument("--default-list-id", dest="default_list_id", default=None)
    ak.add_argument("--label", default=None)
    ak.set_defaults(fn=cmd_api_key)

    pu = sub.add_parser("push", help="push a storyboard package (a dir of scene images + storyboard.json)")
    pu.add_argument("--dir", required=True, help="the authored storyboard package directory")
    pu.add_argument("--list-id", dest="list_id", default=None, help="target list (else the key's default)")
    pu.add_argument("--caption", default=None)
    pu.add_argument("--license", default=None)
    pu.add_argument("--visibility", default=None, help="public | unlisted (default public)")
    pu.add_argument("--no-publish", action="store_true", help="land the reel without publishing it public")
    pu.set_defaults(fn=cmd_push)

    a = p.parse_args(argv)
    try:
        a.fn(a)
    except MnembyteError as e:
        print(f"error {e.status}: {e.detail}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
