#!/usr/bin/env bash
# Simple one-shot remote Docker detection, mirroring the GUI's detection logic.
# Usage: tools/test_remote_docker.sh [user] [host] [port]
# Defaults: user=wzx host=10.12.55.99 port=22
set -eo pipefail
USER_="${1:-wzx}"; HOST_="${2:-10.12.55.99}"; PORT_="${3:-22}"
SSH_ID_OPT=""
if [ -n "${SSH_IDENTITY:-}" ]; then
  SSH_ID_OPT="-i ${SSH_IDENTITY}"
fi
printf '[local] running docker detection on %s@%s:%s\n' "$USER_" "$HOST_" "$PORT_"
set -x
# shellcheck disable=SC2086
ssh -p "$PORT_" -o BatchMode=no -o ConnectTimeout=8 $SSH_ID_OPT "$USER_@$HOST_" bash -s <<'REMOTESCRIPT'
set -euo pipefail
echo "[docker-detect] start" 1>&2
echo "[docker-detect] whoami=$(whoami) shell=$SHELL" 1>&2
# Load user env for rootless docker
[ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc" >/dev/null 2>&1 || true
[ -f "$HOME/.bash_profile" ] && . "$HOME/.bash_profile" >/dev/null 2>&1 || true
[ -f "$HOME/.profile" ] && . "$HOME/.profile" >/dev/null 2>&1 || true
export PATH="$PATH:/usr/bin:/usr/local/bin"
echo "[docker-detect] PATH=$PATH" 1>&2
# Rootless DOCKER_HOST fallback
if [ -z "${DOCKER_HOST:-}" ] && [ -n "${XDG_RUNTIME_DIR:-}" ] && [ -S "$XDG_RUNTIME_DIR/docker.sock" ]; then
  export DOCKER_HOST="unix://$XDG_RUNTIME_DIR/docker.sock"
fi
echo "[docker-detect] DOCKER_HOST=${DOCKER_HOST:-'(default)'}" 1>&2
DOCKERCMD=$(command -v docker 2>/dev/null || true)
if [ -z "$DOCKERCMD" ] && [ -x /usr/bin/docker ]; then DOCKERCMD=/usr/bin/docker; fi
OUT1=""; OUT2=""; OUT3=""; OUT4=""
if [ -n "$DOCKERCMD" ]; then
  echo "[docker-detect] using $DOCKERCMD" 1>&2
  OUT1="$($DOCKERCMD ps --format '{{.Names}}\t{{.ID}}' 2>/dev/null || true)"
  OUT3="$($DOCKERCMD ps -a --format '{{.Names}}\t{{.ID}}' 2>/dev/null || true)"
else
  echo "[docker-detect] docker not found on PATH" 1>&2
fi
OUT2="$(sudo -n docker ps --format '{{.Names}}\t{{.ID}}' 2>/dev/null || true)"
OUT4="$(sudo -n docker ps -a --format '{{.Names}}\t{{.ID}}' 2>/dev/null || true)"
printf '%s\n%s\n%s\n%s\n' "$OUT1" "$OUT2" "$OUT3" "$OUT4" | awk 'NF' | sort -u
REMOTESCRIPT
set +x
echo "[local] Done. If your container name is not listed above, please copy this output back."
