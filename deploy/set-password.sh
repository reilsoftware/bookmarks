#!/bin/sh
# Set the username and password for internet access: writes deploy/auth.caddy with a
# bcrypt hash (never the password itself), then starts or restarts Caddy.
#
#   deploy/set-password.sh USERNAME
set -eu

user="${1:?usage: deploy/set-password.sh USERNAME}"
case "$user" in
  *[!A-Za-z0-9._-]*) echo "Use only letters, digits, dot, dash or underscore in the username." >&2; exit 1 ;;
esac
dir="$(cd "$(dirname "$0")" && pwd)"
compose_file="$dir/../docker-compose.yml"

# QNAP Container Station keeps docker off the PATH, and its CLI can't write ~/.docker.
command -v docker >/dev/null 2>&1 || PATH="/share/CACHEDEV1_DATA/.qpkg/container-station/bin:$PATH"
mkdir -p "$HOME/.docker" 2>/dev/null || export DOCKER_CONFIG="$dir/../.docker"

printf 'Password for %s: ' "$user"
stty -echo; read -r pw; stty echo; echo
printf 'Again: '
stty -echo; read -r pw2; stty echo; echo
[ "$pw" = "$pw2" ] || { echo "Passwords don't match." >&2; exit 1; }
[ "${#pw}" -ge 10 ] || { echo "Use at least 10 characters." >&2; exit 1; }

hash="$(printf '%s\n' "$pw" | docker run --rm -i caddy:2 caddy hash-password)"
case "$hash" in '$2'*) ;; *) echo "Couldn't make the password hash." >&2; exit 1 ;; esac
# If Caddy was started before this file existed, Docker made an empty folder in its place.
[ -d "$dir/auth.caddy" ] && rmdir "$dir/auth.caddy"
umask 077
printf 'basic_auth {\n\t%s %s\n}\n' "$user" "$hash" > "$dir/auth.caddy"
echo "Wrote $dir/auth.caddy"

# Start Caddy if it isn't running yet, and restart it so it reads the new password.
docker compose -f "$compose_file" up -d caddy
docker compose -f "$compose_file" restart caddy
