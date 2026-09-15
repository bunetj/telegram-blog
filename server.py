#!/usr/bin/env python3
"""
server.py — SPA + save/open/upload/delete-avatar + export + cleanup avatars.
"""

import base64
import json
import os
import re
import shutil
import sys
import urllib.parse
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
            used.add(Path(p).name)

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
        if self.path == "/data-bundle":
            return self._handle_data_bundle()
        if self.path.startswith("/data/") or self.path == "/data":
            return self._handle_data_get(self.path)
        if self.path == "/data.json":
            return super().do_GET()
        fs_path = self.translate_path(self.path)
        if os.path.isfile(fs_path):
            return super().do_GET()
        self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith("/data/") or self.path == "/data":
            return self._handle_data_post(self.path)
        if self.path == "/save":
            return self.handle_save()
        if self.path == "/open":
            return self.handle_open()
        if self.path == "/upload":
            return self.handle_upload()
        if self.path == "/delete-avatar":
            return self.handle_delete_avatar()
        if self.path == "/export":
            return self.handle_export()
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

    def handle_export(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            req = json.loads(body.decode("utf-8"))
            out_path = req.get("path", "")
        except Exception as e:
            self.send_error(400, f"Bad JSON: {e}")
            return
        if not out_path:
            self.send_error(400, "Missing path")
            return

        target = Path(out_path)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.send_error(500, f"mkdir failed: {e}")
            return

        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            self.send_error(500, f"data.json unreadable: {e}")
            return

        folders = data.get("folders", []) or []
        channels = data.get("channels", []) or []

        SYSTEM = {"all", "archived", "unfold"}
        folder_by_id = {f["id"]: f for f in folders}

        def sanitize(name):
            bad = '<>:"/\\|?*'
            for ch in bad:
                name = name.replace(ch, "_")
            name = name.strip().rstrip(".")
            return name or "untitled"

        def to_md(html_text):
            if not html_text:
                return ""
            s = html_text
            s = re.sub(
                r'<a\s+href="([^"]*)"[^>]*>([\s\S]*?)</a>',
                lambda m: f"[{m.group(2)}]({m.group(1)})",
                s, flags=re.IGNORECASE
            )
            s = re.sub(r"<b>(.*?)</b>", r"**\1**", s, flags=re.IGNORECASE | re.DOTALL)
            s = re.sub(r"<strong>(.*?)</strong>", r"**\1**", s, flags=re.IGNORECASE | re.DOTALL)
            s = re.sub(r"<i>(.*?)</i>", r"*\1*", s, flags=re.IGNORECASE | re.DOTALL)
            s = re.sub(r"<em>(.*?)</em>", r"*\1*", s, flags=re.IGNORECASE | re.DOTALL)
            s = re.sub(r"</?u>", "", s, flags=re.IGNORECASE)
            s = re.sub(r"<s>(.*?)</s>", r"~~\1~~", s, flags=re.IGNORECASE | re.DOTALL)
            s = re.sub(r"<code>(.*?)</code>", r"`\1`", s, flags=re.IGNORECASE | re.DOTALL)
            s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
            return s

        def channel_dirname(c):
            if c.get("slug"):
                return sanitize(c["slug"])
            return sanitize(c.get("id", "channel"))

        written = 0

        for ch in channels:
            fids = [fid for fid in ch.get("folderIds", []) if fid in folder_by_id and fid not in SYSTEM]
            if not fids:
                continue
            first_fid = fids[0]
            f = folder_by_id[first_fid]
            folder_name = sanitize(f.get("name") or f.get("id") or "folder")
            ch_dir = target / folder_name / channel_dirname(ch)
            ch_dir.mkdir(parents=True, exist_ok=True)
            for p in ch.get("posts", []) or []:
                num = p.get("number")
                if not num:
                    continue
                fp = ch_dir / f"{num}.md"
                fp.write_text(to_md(p.get("body", "")), encoding="utf-8")
                written += 1

        unf_dir_root = target / "_unfolderized"
        for ch in channels:
            fids = [fid for fid in ch.get("folderIds", []) if fid in folder_by_id and fid not in SYSTEM]
            if fids:
                continue
            ch_dir = unf_dir_root / channel_dirname(ch)
            ch_dir.mkdir(parents=True, exist_ok=True)
            for p in ch.get("posts", []) or []:
                num = p.get("number")
                if not num:
                    continue
                fp = ch_dir / f"{num}.md"
                fp.write_text(to_md(p.get("body", "")), encoding="utf-8")
                written += 1

        payload = json.dumps({ "ok": True, "files": written }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_DELETE(self):
        if self.path.startswith("/data/") or self.path == "/data":
            return self._handle_data_delete(self.path)
        self.send_error(405, "DELETE only on /data/")


    # ------------- /data-bundle (single-shot load) -------------
    def _handle_data_bundle(self):
        """Return every folder + channel + post in data/ in one JSON blob."""
        data_root = (ROOT / "data").resolve()
        if not data_root.exists():
            return self._send_json_obj({"folders": [], "channels": []})

        # --- folders.md (raw text; client parses YAML) ---
        folders_md_path = data_root / "folders.md"
        folders_md = ""
        if folders_md_path.exists():
            try:
                folders_md = folders_md_path.read_text(encoding="utf-8")
            except Exception as e:
                folders_md = ""

        channels = []

        def read_channel(folder_path, ch_name):
            ch_dir = (data_root / folder_path / ch_name) if folder_path else (data_root / ch_name)
            if not ch_dir.is_dir():
                return None
            # meta
            meta_text = ""
            mp = ch_dir / "meta.md"
            if mp.exists():
                try: meta_text = mp.read_text(encoding="utf-8")
                except Exception: meta_text = ""
            if not meta_text:
                mp2 = ch_dir / "_channel.md"
                if mp2.exists():
                    try: meta_text = mp2.read_text(encoding="utf-8")
                    except Exception: meta_text = ""
            # posts
            posts = []
            try:
                for f in sorted(ch_dir.iterdir(), key=lambda p: p.name):
                    m = re.match(r"^(\d+)\.md$", f.name)
                    if not m:
                        continue
                    num = int(m.group(1))
                    try:
                        body = f.read_text(encoding="utf-8")
                    except Exception:
                        body = ""
                    posts.append({"number": num, "body": body.rstrip("\n")})
            except Exception:
                pass
            return {
                "folderPath": folder_path,
                "name": ch_name,
                "meta": meta_text,
                "posts": posts,
            }

        # _unfolderized
        unf = data_root / "_unfolderized"
        if unf.is_dir():
            try:
                for d in sorted(unf.iterdir(), key=lambda p: p.name):
                    if d.is_dir():
                        c = read_channel("_unfolderized", d.name)
                        if c: channels.append(c)
            except Exception:
                pass

        # _archived
        arc = data_root / "_archived"
        if arc.is_dir():
            try:
                for d in sorted(arc.iterdir(), key=lambda p: p.name):
                    if d.is_dir():
                        c = read_channel("_archived", d.name)
                        if c: channels.append(c)
            except Exception:
                pass

        # user folder dirs (everything else that's a dir and not reserved)
        reserved = {"_unfolderized", "_archived"}
        try:
            for d in sorted(data_root.iterdir(), key=lambda p: p.name):
                if not d.is_dir(): continue
                if d.name in reserved: continue
                for ch in sorted(d.iterdir(), key=lambda p: p.name):
                    if ch.is_dir():
                        c = read_channel(d.name, ch.name)
                        if c: channels.append(c)
        except Exception:
            pass

        return self._send_json_obj({
            "foldersMd": folders_md,
            "channels": channels,
        })

    def _ok_json(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")


    # ---------------- /data/ file API ----------------
    def _data_root(self):
        return (ROOT / "data").resolve()

    def _safe_data_path(self, rel):
        rel = rel.lstrip("/").replace("\\", "/")
        p = (self._data_root() / rel).resolve()
        try:
            p.relative_to(self._data_root())
        except ValueError:
            return None
        return p

    def _send_json_obj(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text_body(self, text, status=200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _handle_data_get(self, path):
        rel = path[len("/data/"):] if path.startswith("/data/") else path[len("/data"):]
        import urllib.parse as _up
        rel = _up.unquote(rel)
        target = self._safe_data_path(rel)
        if target is None:
            return self._send_text_body("bad path", status=400)
        if target.is_dir():
            try:
                names = sorted(x.name for x in target.iterdir())
            except Exception as e:
                return self._send_text_body(f"list error: {e}", status=500)
            return self._send_json_obj({"dir": True, "entries": names})
        if target.is_file():
            try:
                return self._send_text_body(target.read_text(encoding="utf-8"))
            except Exception as e:
                return self._send_text_body(f"read error: {e}", status=500)
        return self._send_text_body("not found", status=404)

    def _handle_data_post(self, path):
        rel = path[len("/data/"):] if path.startswith("/data/") else path[len("/data"):]
        import urllib.parse as _up
        rel = _up.unquote(rel)
        target = self._safe_data_path(rel)
        if target is None:
            return self._send_text_body("bad path", status=400)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        if rel.endswith("/"):
            try:
                target.mkdir(parents=True, exist_ok=True)
                return self._ok_json()
            except Exception as e:
                return self._send_text_body(f"mkdir failed: {e}", status=500)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            return self._ok_json()
        except Exception as e:
            return self._send_text_body(f"write failed: {e}", status=500)

    def _handle_data_delete(self, path):
        rel = path[len("/data/"):] if path.startswith("/data/") else path[len("/data"):]
        import urllib.parse as _up
        rel = _up.unquote(rel)
        target = self._safe_data_path(rel)
        if target is None:
            return self._send_text_body("bad path", status=400)
        try:
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            return self._ok_json()
        except Exception as e:
            return self._send_text_body(f"delete failed: {e}", status=500)

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
