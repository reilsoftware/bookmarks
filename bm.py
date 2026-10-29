#!/usr/bin/env python3
"""bm: a tiny bookmarks database.

Your bookmarks live in ~/.bookmarks/bookmarks.json (or $BOOKMARKS_HOME/bookmarks.json),
outside this code. Until that file exists, default.json from this folder is shown, and the
first change saves a copy to ~/.bookmarks.

Each bookmark is {"ShortName": ..., "url": ...} (optionally "note" and "added"), grouped
into "folders" (sections). Folders may nest; then top-level folders show as tabs.

Commands:
  python3 bm.py build
      Write ~/.bookmarks/site/index.html (your bookmarks page, self-contained: open it or
      host it anywhere) and ~/.bookmarks/site/browser-import.html (import this in any browser).

  python3 bm.py import FILE [--into "Folder/Sub"] [--replace]
      Merge a browser or Papaly bookmarks export (.html) or another bookmarks.json
      into the database. URLs already in the database are skipped. Then rebuilds.

  python3 bm.py add URL [SHORTNAME] [--folder "Home/Daily"]
      Add one bookmark, then rebuild.

  python3 bm.py export [OUT.html]
      Write only the browser-import file (default: ~/.bookmarks/site/browser-import.html).
"""
import argparse
import html
import json
import os
import shutil
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "template.html"
DEFAULT_DB = ROOT / "default.json"
# Personal data stays out of the code folder, so the code can be published as is.
DATA_DIR = Path(os.environ.get("BOOKMARKS_HOME") or Path.home() / ".bookmarks").expanduser()
DB_PATH = DATA_DIR / "bookmarks.json"
SITE = DATA_DIR / "site"
DATA_MARKER = '/*__DATA__*/{"folders": []}'


def short(path):
    """path with the home folder shown as ~, for messages."""
    try:
        return "~/" + str(Path(path).relative_to(Path.home()))
    except ValueError:
        return str(path)


# ---------- database ----------

def db_source():
    """The file load_db reads: your bookmarks if they exist, else the bundled default."""
    return DB_PATH if DB_PATH.exists() else DEFAULT_DB


def load_db():
    path = db_source()
    if not path.exists():
        return {"title": "Bookmarks", "folders": []}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def tidy(node):
    """Return node with keys in a stable order and empty lists dropped, so the JSON stays hand-editable."""
    out = {}
    for key in ("title", "name"):
        if node.get(key):
            out[key] = node[key]
    bookmarks = [tidy_bookmark(b) for b in node.get("bookmarks", []) if b.get("url")]
    if bookmarks:
        out["bookmarks"] = bookmarks
    folders = [tidy(f) for f in node.get("folders", [])]
    if folders:
        out["folders"] = folders
    return out


def tidy_bookmark(b):
    out = {"ShortName": b.get("ShortName") or b["url"], "url": b["url"]}
    for key in ("note", "added"):
        if b.get(key):
            out[key] = b[key]
    return out


def save_db(db):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        shutil.copy2(DB_PATH, DB_PATH.with_suffix(".json.bak"))
    tmp = DB_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(tidy(db), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, DB_PATH)


def all_urls(node):
    urls = {b["url"] for b in node.get("bookmarks", [])}
    for f in node.get("folders", []):
        urls |= all_urls(f)
    return urls


def count(node):
    return len(node.get("bookmarks", [])) + sum(count(f) for f in node.get("folders", []))


def folder_at(db, path):
    """Find (creating as needed) the folder at a "A/B/C" path. An empty path is the root."""
    node = db
    for name in [p.strip() for p in (path or "").split("/") if p.strip()]:
        folders = node.setdefault("folders", [])
        nxt = next((f for f in folders if f.get("name") == name), None)
        if nxt is None:
            nxt = {"name": name}
            folders.append(nxt)
        node = nxt
    return node


def merge(dst, src, seen):
    """Merge folder tree src into dst, matching folders by name and skipping URLs already in the
    database (seen). Repeats within src itself are kept, since those were there on purpose."""
    added = 0
    for b in src.get("bookmarks", []):
        if b.get("url") and b["url"] not in seen:
            dst.setdefault("bookmarks", []).append(b)
            added += 1
    for f in src.get("folders", []):
        existing = next((x for x in dst.get("folders", []) if x.get("name") == f.get("name")), None)
        target = existing or {"name": f.get("name") or "Untitled"}
        n = merge(target, f, seen)
        if existing is None and (n or not (f.get("bookmarks") or f.get("folders"))):
            dst.setdefault("folders", []).append(target)
        added += n
    return added


# ---------- Netscape bookmark files (what browsers and Papaly import/export) ----------

class NetscapeParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = {"folders": [], "bookmarks": []}
        self.stack = [self.root]
        self.pending = None   # folder named by the last <H3>; it owns the next <DL>
        self.capture = None   # "h3" or "a" while reading their text
        self.text = ""
        self.attrs = {}
        self.last = None      # last bookmark, for a following <DD> description
        self.dd = None

    def _end_dd(self):
        if self.dd is not None and self.last is not None and self.dd.strip():
            self.last["note"] = " ".join(self.dd.split())
        self.dd = None

    def handle_starttag(self, tag, attrs):
        if tag in ("dt", "dl", "h3", "a"):
            self._end_dd()
        if tag in ("h3", "a"):
            self.capture, self.text, self.attrs = tag, "", dict(attrs)
        elif tag == "dl":
            self.stack.append(self.pending if self.pending is not None else self.stack[-1])
            self.pending = None
        elif tag == "dd":
            self.dd = ""

    def handle_endtag(self, tag):
        if tag == "h3" and self.capture == "h3":
            folder = {"name": " ".join(self.text.split()) or "Untitled", "folders": [], "bookmarks": []}
            self.stack[-1]["folders"].append(folder)
            self.pending, self.capture = folder, None
        elif tag == "a" and self.capture == "a":
            url = (self.attrs.get("href") or "").strip()
            if url:
                b = {"ShortName": " ".join(self.text.split()) or url, "url": url}
                if (self.attrs.get("add_date") or "").isdigit():
                    b["added"] = int(self.attrs["add_date"])
                self.stack[-1]["bookmarks"].append(b)
                self.last = b
            self.capture = None
        elif tag == "dl":
            self._end_dd()
            if len(self.stack) > 1:
                self.stack.pop()

    def handle_data(self, data):
        if self.capture:
            self.text += data
        elif self.dd is not None:
            self.dd += data


def parse_netscape(text):
    p = NetscapeParser()
    p.feed(text)
    p.close()
    return p.root


def to_netscape(db):
    esc = lambda s: html.escape(str(s), quote=True)
    now = int(time.time())
    title = esc(db.get("title") or "Bookmarks")
    out = ["<!DOCTYPE NETSCAPE-Bookmark-file-1>",
           '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
           f"<TITLE>{title}</TITLE>", f"<H1>{title}</H1>", "<DL><p>"]

    def emit(node, ind):
        for b in node.get("bookmarks", []):
            out.append(f'{ind}<DT><A HREF="{esc(b["url"])}" ADD_DATE="{b.get("added", now)}">{esc(b.get("ShortName") or b["url"])}</A>')
            if b.get("note"):
                out.append(f"{ind}<DD>{esc(b['note'])}")
        for f in node.get("folders", []):
            out.append(f'{ind}<DT><H3 ADD_DATE="{now}">{esc(f.get("name", "Untitled"))}</H3>')
            out.append(f"{ind}<DL><p>")
            emit(f, ind + "    ")
            out.append(f"{ind}</DL><p>")

    emit(db, "    ")
    out += ["</DL><p>", ""]
    return "\n".join(out)


# ---------- commands ----------

def render_page(db, **extra):
    """The bookmarks page with db embedded. server.py passes _server=True to turn on editing."""
    template = TEMPLATE.read_text(encoding="utf-8")
    if DATA_MARKER not in template:
        sys.exit(f"error: {TEMPLATE.name} is missing the data marker {DATA_MARKER}")
    data = dict(tidy(db), **extra)
    # Escape "<" so a bookmark title can never close the <script> tag.
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    return template.replace(DATA_MARKER, payload)


def build(db, quiet=False):
    SITE.mkdir(parents=True, exist_ok=True)
    page = render_page(db, _built=time.strftime("%Y-%m-%d %H:%M"))
    (SITE / "index.html").write_text(page, encoding="utf-8")
    (SITE / "browser-import.html").write_text(to_netscape(db), encoding="utf-8")
    if not quiet:
        print(f"built {short(SITE)}/index.html and browser-import.html ({count(db)} bookmarks)")


def cmd_import(args):
    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    src = json.loads(text) if text.lstrip().startswith("{") else parse_netscape(text)
    found = count(src)
    if not found:
        sys.exit(f"error: no bookmarks found in {args.file}")
    db = load_db()
    if args.replace:
        db = {"title": db.get("title", "Bookmarks")}
    added = merge(folder_at(db, args.into), src, all_urls(db))
    save_db(db)
    print(f"imported {added} of {found} bookmarks from {args.file} ({found - added} already present)")
    build(db)


def cmd_add(args):
    url = args.url if "://" in args.url or args.url.startswith(("javascript:", "mailto:")) else "https://" + args.url
    db = load_db()
    if url in all_urls(db):
        sys.exit(f"already in bookmarks: {url}")
    name = args.shortname or (urlparse(url).hostname or url).removeprefix("www.")
    folder_at(db, args.folder).setdefault("bookmarks", []).append({"ShortName": name, "url": url})
    save_db(db)
    print(f"added {name} -> {url}" + (f" in {args.folder}" if args.folder else ""))
    build(db)


def cmd_export(args):
    out = Path(args.out) if args.out else SITE / "browser-import.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    db = load_db()
    out.write_text(to_netscape(db), encoding="utf-8")
    print(f"wrote {out} ({count(db)} bookmarks)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build").set_defaults(fn=lambda a: build(load_db()))
    p = sub.add_parser("import")
    p.add_argument("file")
    p.add_argument("--into", default="", help='put everything under this folder path, e.g. "Imported/Chrome"')
    p.add_argument("--replace", action="store_true", help="discard the current database first (a .bak is kept)")
    p.set_defaults(fn=cmd_import)
    p = sub.add_parser("add")
    p.add_argument("url")
    p.add_argument("shortname", nargs="?")
    p.add_argument("--folder", default="", help='folder path, e.g. "Home/Daily"')
    p.set_defaults(fn=cmd_add)
    p = sub.add_parser("export")
    p.add_argument("out", nargs="?")
    p.set_defaults(fn=cmd_export)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
