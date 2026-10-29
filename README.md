# Bookmarks

A self-hosted bookmarks page, a replacement for Papaly. Your bookmarks are a plain JSON file;
a small local server lets you edit them in the browser. Needs only Python 3, no packages.

## Run it

```bash
python3 server.py --open      # http://localhost:8765
```

On the page:

- **Search** at the top (`/` to focus, Enter opens the first match, Esc clears).
- **+ next to "Bookmarks"** adds a section.
- **+ in a section header** opens a box: paste a URL, Enter (the server fetches its icon and
  title), then type the ShortName, Enter. Esc cancels.
- **×** on a link deletes it (after a confirm). An empty section shows an × in its header.
- **Drag** a link to reorder it or move it to another section; drag a section by its header to
  reorder sections. Click a header to collapse it.
- **Columns 1 2 3 4** at the bottom; your choice is remembered.
- **Generate HTML** downloads a standalone, read-only copy of the page to host anywhere.
- **Generate bookmarks** downloads a file to import into Chrome, Safari, Firefox or Edge.
- **Download JSON** downloads your bookmarks database: a backup you can edit or restore.

The server listens on 127.0.0.1 only. To use it from other devices on your network, run
`python3 server.py --host 0.0.0.0`; anyone on that network can then edit your bookmarks.

## Run it in Docker (e.g. on a NAS), reachable from the internet

`docker-compose.yml` runs the server plus [Caddy](https://caddyserver.com/) in front of it:

- **At home** (LAN or Tailscale): `http://<nas>:1234`, no login.
- **From the internet**: `https://your.domain`, with a username and password. Caddy gets a free
  Let's Encrypt certificate automatically.

```bash
cp .env.example .env              # set DOMAIN; change ports if they're taken
deploy/set-password.sh kevin      # choose the internet password (stored only as a hash)
docker compose up -d --build
```

On your router, forward **80 → NAS:8880** and **443 → NAS:8443** (the `HTTP_PORT` and
`HTTPS_PORT` in `.env`). Do **not** forward 1234: it has no password. As a backstop the server
refuses direct connections from public internet addresses, so a mistaken forward gets a 403.

Your bookmarks live in `./data/` next to the compose file (back it up). To change the
password, run `deploy/set-password.sh` again.

**Updating.** From the machine with this code, copy it to the server and rebuild in one step
(over SSH; the server's `.env`, `data/` and password are left alone):

```bash
deploy/update-server.sh you@your-nas:/path/to/bookmarks
```

On the server itself: `docker compose up -d --build` rebuilds and restarts; `docker compose
restart` just restarts.

## Where your bookmarks live

Your data is kept outside this folder, in `~/.bookmarks/`:

- `bookmarks.json`: your bookmarks (the previous version is kept as `bookmarks.json.bak`)
- `icons/`: cached favicons; delete it to refetch
- `site/`: the generated static page and browser-import file

Until `~/.bookmarks/bookmarks.json` exists, the page shows [`default.json`](default.json) from
this folder, and your first edit saves your own copy to `~/.bookmarks`. To keep your data
somewhere else, set `BOOKMARKS_HOME`, e.g. `BOOKMARKS_HOME=~/Dropbox/bookmarks python3 server.py`.

You can edit `bookmarks.json` by hand; the page picks changes up on reload.

## The format

```json
{
  "title": "Bookmarks",
  "folders": [
    { "name": "Daily", "bookmarks": [
      { "ShortName": "Weather", "url": "https://www.windy.com/" }
    ] }
  ]
}
```

Each folder is a section. A bookmark may also have a `"note"` (shown as a tooltip). Folders may
nest; if any top-level folder has subfolders, top-level folders show as tabs.

## Command line

```bash
python3 bm.py build                                   # ~/.bookmarks/site/index.html + browser-import.html
python3 bm.py add github.com GitHub --folder Code     # add one link
python3 bm.py import ~/Downloads/bookmarks.html       # merge a browser export (skips known URLs)
python3 bm.py import file.html --into "From Chrome"   # ...under one folder
python3 bm.py import file.html --replace              # start over (bookmarks.json.bak is kept)
python3 bm.py export ~/Desktop/bookmarks.html         # browser-import file only
```

## Files

- `server.py`: local server (page, editing API, icon fetching)
- `bm.py`: database helpers, browser import/export, static build
- `template.html`: the page (HTML, CSS and JS)
- `default.json`: starter bookmarks shown until you have your own
