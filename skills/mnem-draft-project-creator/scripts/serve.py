#!/usr/bin/env python3
"""Dev server for the video maker: serves the skill directory AND accepts take drops.

`PUT /drop/<filename>` writes the request body into the draftbook's inbox
(raw uploads land in inbox first — the LBFS convention), so recording a take
and keeping it works even in embedded browsers that have no download manager.
Stdlib only, like everything else in this skill.

Usage:
  serve.py [--port 8712] [--directory <skill dir>] [--drop <dir>]
  # default drop dir: <directory>/examples/draftbooks/the-stick/inbox/takes
"""
from __future__ import annotations
import argparse, http.server, json, os, re, shutil, subprocess
from functools import partial

FFMPEG = shutil.which("ffmpeg") or ("/opt/homebrew/bin/ffmpeg" if os.path.exists("/opt/homebrew/bin/ffmpeg") else None)

def to_mp4(src: str) -> str | None:
    """WebM -> MP4 (H.264/AAC) so QuickTime + Instagram can open the take. None if unavailable/failed."""
    if not FFMPEG or not src.endswith(".webm"):
        return None
    dest = src[:-5] + ".mp4"
    r = subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", src,
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", dest],
                       capture_output=True, timeout=300)
    return dest if r.returncode == 0 and os.path.exists(dest) else None


class Handler(http.server.SimpleHTTPRequestHandler):
    drop_dir = "."
    base_dir = "."

    def do_PUT(self):
        if not self.path.startswith("/drop/"):
            self.send_error(404, "PUT only accepts /drop/<filename>"); return
        name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(self.path[len("/drop/"):])) or "take.webm"
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 2_000_000_000:
            self.send_error(400, "missing or oversized body"); return
        os.makedirs(self.drop_dir, exist_ok=True)
        dest = os.path.join(self.drop_dir, name)
        with open(dest, "wb") as f:
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk: break
                f.write(chunk); remaining -= len(chunk)
        mp4 = to_mp4(dest)
        body = json.dumps({"saved": os.path.relpath(dest, self.base_dir), "bytes": length,
                           "mp4": os.path.relpath(mp4, self.base_dir) if mp4 else None}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8712)
    ap.add_argument("--directory", default=".")
    ap.add_argument("--drop", default=None)
    a = ap.parse_args()
    Handler.base_dir = os.path.abspath(a.directory)
    Handler.drop_dir = os.path.abspath(a.drop or os.path.join(a.directory, "examples/draftbooks/the-stick/inbox/takes"))
    handler = partial(Handler, directory=Handler.base_dir)
    print("serving %s on :%d · takes drop -> %s" % (Handler.base_dir, a.port, Handler.drop_dir))
    http.server.ThreadingHTTPServer(("127.0.0.1", a.port), handler).serve_forever()


if __name__ == "__main__":
    main()
