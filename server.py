#!/usr/bin/env python3
"""Local bookmarks server: serves the page and saves edits to ~/.bookmarks/bookmarks.json.

  python3 server.py                 # http://127.0.0.1:8765
  python3 server.py --port 9000 --open

Until ~/.bookmarks/bookmarks.json exists the page shows default.json; the first edit
creates it. Set BOOKMARKS_HOME to keep your data somewhere else.

Favicons are fetched from each site (which also works for LAN addresses like 192.168.x.x)
and cached in ~/.bookmarks/icons/. Delete that folder to refetch them.
"""
import argparse
import ipaddress
import json
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urljoin, urlparse

import bm

ICONS = bm.DATA_DIR / "icons"
DB_LOCK = threading.Lock()
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
# Only used to fetch titles and icons; many LAN devices have self-signed certificates.
INSECURE = ssl.create_default_context()
INSECURE.check_hostname = False
INSECURE.verify_mode = ssl.CERT_NONE


# ---------- URLs, titles and icons ----------

def normalize_url(raw):
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Enter a URL")
    if re.match(r"^[a-z][a-z0-9+.-]*:(//)?", raw, re.I) and not re.match(r"^[\w.-]+:\d", raw):
        return raw
    host = re.split(r"[/:?#]", raw, maxsplit=1)[0]
    local = re.fullmatch(r"[\d.]+", host) or "." not in host or host.endswith((".local", ".lan", ".home", ".ts.net"))
    return ("http://" if local else "https://") + raw


def is_public(host):
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return "." in host and not host.endswith((".local", ".lan", ".home", ".ts.net"))


def fetch(url, limit=600_000, timeout=5):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout, context=INSECURE) as r:
        return r.geturl(), r.headers.get("Content-Type", ""), r.read(limit)


class HeadParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.icons, self.touch_icons, self.title, self._in_title = [], [], None, False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        rel = (a.get("rel") or "").lower().split()
        if tag == "link" and a.get("href"):
            if "icon" in rel:
                self.icons.append(a["href"])
            elif "apple-touch-icon" in rel:
                self.touch_icons.append(a["href"])
        elif tag == "title" and self.title is None:
            self.title, self._in_title = "", True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def page_info(url):
    """Return (title, [candidate icon URLs], reachable) from the page's HTML."""
    try:
        final, ctype, body = fetch(url, timeout=3)
    except urllib.error.HTTPError:
        return None, [], True    # the server answered (e.g. 401/404), so /favicon.ico may still work
    except Exception:
        return None, [], False   # no answer at all; don't wait on the same host again
    if "html" not in ctype and not body.lstrip()[:1] == b"<":
        return None, [], True
    p = HeadParser()
    try:
        p.feed(body.decode("utf-8", "replace"))
    except Exception:
        pass
    title = " ".join((p.title or "").split())[:100] or None
    return title, [urljoin(final, h) for h in p.icons + p.touch_icons if not h.startswith("data:")], True


def looks_like_image(ctype, data):
    if not data or data.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        return False
    return (ctype.startswith("image/") or data[:4] in (b"\x00\x00\x01\x00", b"\x89PNG")
            or data[:3] == b"GIF" or b"<svg" in data[:500])


_host_locks, _host_locks_guard = {}, threading.Lock()


def icon_for(url, page=None):
    """(content_type, bytes) of the site's icon, cached per host in icons/; None if it has none."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    key = re.sub(r"[^a-z0-9.-]", "_", p.netloc.lower())
    with _host_locks_guard:
        lock = _host_locks.setdefault(key, threading.Lock())
    with lock:
        img, ct, none = ICONS / f"{key}.img", ICONS / f"{key}.type", ICONS / f"{key}.none"
        if img.exists():
            return ct.read_text(), img.read_bytes()
        if none.exists():
            return None
        _, candidates, reachable = page or page_info(url)
        if reachable:
            candidates = candidates + [urljoin(url, "/favicon.ico")]
        if is_public(p.hostname):
            candidates.append(f"https://icons.duckduckgo.com/ip3/{p.hostname}.ico")
        ICONS.mkdir(parents=True, exist_ok=True)
        for c in dict.fromkeys(candidates):
            try:
                _, ctype, data = fetch(c, limit=300_000, timeout=3)
            except Exception:
                continue
            if looks_like_image(ctype, data):
                ctype = ctype.split(";")[0] if ctype.startswith("image/") else "image/x-icon"
                img.write_bytes(data)
                ct.write_text(ctype)
                return ctype, data
        none.touch()
        return None


# ---------- database edits ----------

def node_at(db, path):
    node = db
    for i in path:
        node = node["folders"][int(i)]
    return node


def add_bookmark(path, url, name):
    with DB_LOCK:
        db = bm.load_db()
        node = node_at(db, path)
        if any(b["url"] == url for b in node.get("bookmarks", [])):
            raise LookupError(f"Already in {node.get('name', 'this section')}")
        node.setdefault("bookmarks", []).append({"ShortName": name, "url": url})
        bm.save_db(db)
        bm.build(db, quiet=True)
        return bm.tidy(db)


def delete_bookmark(path, index, url):
    with DB_LOCK:
        db = bm.load_db()
        items = node_at(db, path).get("bookmarks", [])
        if not (0 <= index < len(items)) or items[index]["url"] != url:
            raise LookupError("bookmarks.json changed since this page loaded; reload and try again")
        del items[index]
        bm.save_db(db)
        bm.build(db, quiet=True)
        return bm.tidy(db)


def add_section(path, name):
    name = (name or "").strip()
    if not name:
        raise ValueError("Enter a section name")
    with DB_LOCK:
        db = bm.load_db()
        folders = node_at(db, path).setdefault("folders", [])
        if any(f.get("name") == name for f in folders):
            raise LookupError(f"There is already a section called {name}")
        folders.append({"name": name})
        bm.save_db(db)
        bm.build(db, quiet=True)
        return bm.tidy(db)


def delete_section(path, name):
    """Delete a section, but only when it has no bookmarks and no subsections."""
    if not path:
        raise LookupError("That section can't be deleted")
    with DB_LOCK:
        db = bm.load_db()
        folders = node_at(db, path[:-1])["folders"]
        i = int(path[-1])
        if not (0 <= i < len(folders)) or folders[i].get("name") != name:
            raise LookupError("bookmarks.json changed since this page loaded; reload and try again")
        if folders[i].get("bookmarks") or folders[i].get("folders"):
            raise LookupError(f"{name} isn't empty; delete or move its links first")
        del folders[i]
        bm.save_db(db)
        bm.build(db, quiet=True)
        return bm.tidy(db)


def move_section(path, to_index):
    """Reorder a section among its siblings; to_index is its position as the page saw it."""
    if not path:
        raise LookupError("That section can't be moved")
    with DB_LOCK:
        db = bm.load_db()
        folders = node_at(db, path[:-1])["folders"]
        i = int(path[-1])
        item = folders.pop(i)
        if i < to_index:
            to_index -= 1
        folders.insert(max(0, min(to_index, len(folders))), item)
        bm.save_db(db)
        bm.build(db, quiet=True)
        return bm.tidy(db)


def move_bookmark(src_path, src_index, url, dst_path, dst_index):
    """Move a bookmark; dst_index is its position in the destination list as the page saw it."""
    with DB_LOCK:
        db = bm.load_db()
        src = node_at(db, src_path).setdefault("bookmarks", [])
        if not (0 <= src_index < len(src)) or src[src_index]["url"] != url:
            raise LookupError("bookmarks.json changed since this page loaded; reload and try again")
        dst = node_at(db, dst_path).setdefault("bookmarks", [])
        item = src.pop(src_index)
        if src is dst and src_index < dst_index:
            dst_index -= 1
        dst.insert(max(0, min(dst_index, len(dst))), item)
        bm.save_db(db)
        bm.build(db, quiet=True)
        return bm.tidy(db)


# ---------- HTTP ----------

TAILSCALE = ipaddress.ip_network("100.64.0.0/10")


def is_local_client(addr):
    """True for this machine, the home network, Tailscale and Docker's own networks.
    Internet visitors must come through Caddy (which asks for a password), so a direct
    connection from a public address means a port was forwarded that shouldn't be."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local or (ip.version == 4 and ip in TAILSCALE)


class Handler(BaseHTTPRequestHandler):
    def handle_one_request(self):
        if is_local_client(self.client_address[0]):
            return super().handle_one_request()
        # Refuse before reading the request: no page, no API, no icon fetching.
        self.raw_requestline = self.rfile.readline(65537)
        if self.raw_requestline and self.parse_request():
            self.send_error(403, "Use the HTTPS address with your password")
        self.close_connection = True

    def send(self, status, ctype, body, headers=()):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj, status=200):
        self.send(status, "application/json", json.dumps(obj, ensure_ascii=False).encode(), [("Cache-Control", "no-store")])

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            page = bm.render_page(bm.load_db(), _server=True).encode()
            return self.send(200, "text/html; charset=utf-8", page, [("Cache-Control", "no-store")])
        if u.path == "/api/bookmarks":
            return self.json(bm.tidy(bm.load_db()))
        if u.path == "/download/bookmarks.json":
            body = json.dumps(bm.tidy(bm.load_db()), indent=2, ensure_ascii=False) + "\n"
            name = f"bookmarks-{time.strftime('%Y-%m-%d')}.json"
            return self.send(200, "application/json; charset=utf-8", body.encode(),
                             [("Content-Disposition", f'attachment; filename="{name}"'), ("Cache-Control", "no-store")])
        if u.path in ("/download/static.html", "/download/browser-import.html"):
            # Also refresh site/, so the files there match what was just downloaded.
            db = bm.load_db()
            bm.build(db, quiet=True)
            stamp = time.strftime("%Y-%m-%d")
            if u.path.endswith("static.html"):
                body, name = bm.render_page(db, _built=time.strftime("%Y-%m-%d %H:%M")), f"bookmarks-{stamp}.html"
            else:
                body, name = bm.to_netscape(db), f"bookmarks-import-{stamp}.html"
            return self.send(200, "text/html; charset=utf-8", body.encode(),
                             [("Content-Disposition", f'attachment; filename="{name}"'), ("Cache-Control", "no-store")])
        if u.path == "/icon":
            icon = icon_for(parse_qs(u.query).get("url", [""])[0])
            if not icon:
                return self.send(404, "text/plain", b"no icon", [("Cache-Control", "max-age=3600")])
            return self.send(200, icon[0], icon[1], [("Cache-Control", "max-age=86400")])
        self.send(404, "text/plain", b"not found")

    def do_POST(self):
        # Only accept JSON from this page: a JSON POST from another site needs a CORS preflight,
        # which this server never grants, and the Origin check covers the rest.
        origin = self.headers.get("Origin")
        if not self.headers.get("Content-Type", "").startswith("application/json") or (
                origin and urlparse(origin).netloc != self.headers.get("Host")):
            return self.json({"error": "forbidden"}, 403)
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 1_000_000)) or b"{}")
            if self.path == "/api/resolve":
                url = normalize_url(body.get("url"))
                page = page_info(url)
                icon = icon_for(url, page)
                return self.json({"url": url, "title": page[0], "icon": bool(icon)})
            if self.path == "/api/add":
                url = normalize_url(body.get("url"))
                name = (body.get("ShortName") or "").strip() or (urlparse(url).hostname or url).removeprefix("www.")
                return self.json(add_bookmark(body.get("path", []), url, name))
            if self.path == "/api/delete":
                return self.json(delete_bookmark(body.get("path", []), int(body.get("index", -1)), body.get("url")))
            if self.path == "/api/add-section":
                return self.json(add_section(body.get("path", []), body.get("name")))
            if self.path == "/api/delete-section":
                return self.json(delete_section(body.get("path", []), body.get("name")))
            if self.path == "/api/move-section":
                return self.json(move_section(body.get("path", []), int(body.get("toIndex", 0))))
            if self.path == "/api/move":
                return self.json(move_bookmark(body.get("path", []), int(body.get("index", -1)), body.get("url"),
                                               body.get("toPath", []), int(body.get("toIndex", 0))))
        except (ValueError, LookupError) as e:
            return self.json({"error": str(e)}, 409 if isinstance(e, LookupError) else 400)
        except (KeyError, IndexError, TypeError):
            return self.json({"error": "That section no longer exists; reload the page"}, 409)
        self.json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):
        if not self.path.startswith("/icon"):
            super().log_message(fmt, *args)


def main():
    ap = argparse.ArgumentParser(description="Serve the bookmarks page with editing.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="keep the default unless you want other machines to edit too")
    ap.add_argument("--open", action="store_true", help="open the page in your browser")
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}/"
    if bm.DB_PATH.exists():
        print(f"Using {bm.short(bm.DB_PATH)}")
    else:
        print(f"No {bm.short(bm.DB_PATH)} yet: showing {bm.DEFAULT_DB.name}; your first edit creates it")
    print(f"Bookmarks at {url}  (Ctrl-C to stop)", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
