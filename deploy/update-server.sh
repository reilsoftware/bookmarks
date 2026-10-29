#!/bin/sh
# Copy this code to the server over SSH, then rebuild and restart the container there.
# Only code is copied: the server's .env, data/ (your bookmarks) and password are left alone.
#
#   deploy/update-server.sh USER@HOST:/path/to/bookmarks
set -eu

target="${1:?usage: deploy/update-server.sh USER@HOST:/path/to/bookmarks}"
host="${target%%:*}"
path="${target#*:}"
[ "$host" != "$target" ] && [ -n "$path" ] || { echo "Give the server as USER@HOST:/path" >&2; exit 1; }
cd "$(dirname "$0")/.."

echo "Copying code to $host:$path"
COPYFILE_DISABLE=1 tar --no-xattrs -czf - \
  Dockerfile .dockerignore docker-compose.yml .env.example README.md \
  bm.py server.py template.html default.json \
  deploy/Caddyfile deploy/set-password.sh deploy/update-server.sh |
  ssh "$host" "mkdir -p '$path' && cd '$path' && tar -xzf - 2>/dev/null; chmod +x deploy/*.sh"

echo "Rebuilding and restarting"
ssh "$host" "cd '$path' && sh -s" <<'EOF'
set -e
# QNAP Container Station keeps docker off the PATH, and its CLI can't write its usual
# config folder, so keep the CLI's settings next to the app.
command -v docker >/dev/null 2>&1 || PATH="/share/CACHEDEV1_DATA/.qpkg/container-station/bin:$PATH"
export DOCKER_CONFIG="${DOCKER_CONFIG:-$PWD/.docker}"
docker compose up -d --build bookmarks
# Caddy runs only once a password has been set (deploy/set-password.sh).
if [ -f deploy/auth.caddy ]; then docker compose up -d caddy; fi
docker compose ps --format '{{.Service}}: {{.Status}}'
EOF
