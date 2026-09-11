#!/usr/bin/env python3
"""
server.py — SPA + save/open/upload/delete-avatar + cleanup avatars.
"""

import base64
import json
import os
import re
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data.json"
MEDIA_DIR = ROOT / "media" / "avatars"
ROOTS_FILE = ROOT / "allowed_roots.txt"
PORT = 49188


def load_allowed_roots():
    roots = []
    if ROOTS_FILE.exists():
        for line in ROOTS_FILE.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            try:
                roots.append(Path(s).resolve())
            except Exception as e:
                print(f"[warn] bad root '{s}': {e}", file=sys.stderr)
    if not roots:
        print("[warn] allowed_roots.txt empty or missing; /open disabled", file=sys.stderr)
    return roots


ALLOWED_ROOTS = load_allowed_roots()


def is_inside_allowed(path: Path) -> bool:
    try:
        rp = path.resolve()
    except Exception:
        return False
    for root in ALLOWED_ROOTS:
        try:
            rp.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def ensure_media_dir():
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)


def cleanup_orphan_avatars():
    """Удаляет media/avatars/*.jpg, которых нет в data.json.
    Если data.json отсутствует/битый — ничего не удаляем.
    """
    ensure_media_dir()
    if not DATA_FILE.exists():
        return 0
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[cleanup] data.json unreadable, skip: {e}", file=sys.stderr)
        return 0

    used = set()
    for ch in data.get("channels", []) or []:
        p = ch.get("avatarPath")
        if p:
            used.add(Path(p).name)  # basename

    removed = 0
    for f in MEDIA_DIR.glob("*.jpg"):
        if f.name not in used:
            try:
                f.unlink()
                removed += 1
            except Exception as e:
                print(f"[cleanup] failed to remove {f}: {e}", file=sys.stderr)
    return removed


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        if self.path == "/data.json":
            return super().do_GET()
        fs_path = self.translate_path(self.path)
        if os.path.isfile(fs_path):
            return super().do_GET()
        self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        if self.path == "/save":
            return self.handle_save()
        if self.path == "/open":
            return self.handle_open()
        if self.path == "/upload":
            return self.handle_upload()
        if self.path == "/delete-avatar":
            return self.handle_delete_avatar()
        self.send_error(404, "Not found")

    def handle_save(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as e:
            self.send_error(400, f"Bad JSON: {e}")
            return
        tmp = DATA_FILE.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(DATA_FILE)
        except Exception as e:
            self.send_error(500, f"Write failed: {e}")
            return
        self._ok_json()

    def handle_open(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            req = json.loads(body.decode("utf-8"))
            raw_path = req.get("path", "")
        except Exception as e:
            self.send_error(400, f"Bad JSON: {e}")
            return
        if not raw_path:
            self.send_error(400, "Missing path")
            return
        p = Path(raw_path)
        if not p.is_absolute():
            self.send_error(400, "Path must be absolute")
            return
        if not is_inside_allowed(p):
            self.send_error(403, "Path outside allowed roots")
            return
        if not p.exists():
            self.send_error(404, f"Not found: {p}")
            return
        print(f"[open] {p}", flush=True)
        try:
            os.startfile(str(p))
            print(f"[open] OK: {p}", flush=True)
        except Exception as e:
            print(f"[open] FAIL: {e}", flush=True)
            self.send_error(500, f"Open failed: {e}")
            return
        self._ok_json()

    def handle_upload(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            req = json.loads(body.decode("utf-8"))
            cid = req.get("id", "")
            data_url = req.get("data", "")
        except Exception as e:
            self.send_error(400, f"Bad JSON: {e}")
            return

        if not cid or not re.fullmatch(r"[A-Za-z0-9_-]+", cid):
            self.send_error(400, "Bad id")
            return
        if not data_url.startswith("data:image/"):
            self.send_error(400, "Bad data url")
            return

        # data:image/jpeg;base64,....
        try:
            head, b64 = data_url.split(",", 1)
            raw = base64.b64decode(b64)
        except Exception as e:
            self.send_error(400, f"Bad base64: {e}")
            return

        ensure_media_dir()
        out = MEDIA_DIR / f"{cid}.jpg"
        try:
            out.write_bytes(raw)
        except Exception as e:
            self.send_error(500, f"Write failed: {e}")
            return

        rel = f"media/avatars/{cid}.jpg"
        payload = json.dumps({ "path": rel }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def handle_delete_avatar(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            req = json.loads(body.decode("utf-8"))
            cid = req.get("id", "")
        except Exception as e:
            self.send_error(400, f"Bad JSON: {e}")
            return
        if not cid or not re.fullmatch(r"[A-Za-z0-9_-]+", cid):
            self.send_error(400, "Bad id")
            return
        f = MEDIA_DIR / f"{cid}.jpg"
        if f.exists():
            try:
                f.unlink()
                print(f"[delete-avatar] removed {f}", flush=True)
            except Exception as e:
                self.send_error(500, f"Delete failed: {e}")
                return
        self._ok_json()

    def _ok_json(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, fmt, *args):
        sys.stderr.write("[server] " + (fmt % args) + "\n")


def main():
    ensure_media_dir()
    removed = cleanup_orphan_avatars()
    httpd = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Subscriptions server running at http://localhost:{PORT}/")
    print(f"Data file:      {DATA_FILE}")
    print(f"Media dir:      {MEDIA_DIR}")
    print(f"Allowed roots:  {ROOTS_FILE}")
    for r in ALLOWED_ROOTS:
        print(f"  - {r}")
    print(f"[cleanup] removed {removed} orphan avatars")
    print("Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()