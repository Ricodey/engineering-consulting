#!/usr/bin/env bash
# Build a release and switch it live, or roll back to the previous one.
# Setup is deploy/setup-server.sh, run once on the server as root.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
DEPLOY_PATH=/var/www/datum

die() {
  echo "deploy.sh: $*" >&2
  exit 1
}

usage() {
  echo "usage: ./deploy/deploy.sh [--dry-run|rollback]" >&2
  exit 2
}

stage=
cleanup() {
  if [ -n "$stage" ]; then
    rm -rf "$stage"
  fi
}
trap cleanup EXIT

action=deploy
case "${1:-}" in
  "") ;;
  --dry-run) action=dry-run ;;
  rollback) action=rollback ;;
  *) usage ;;
esac
if [ "$#" -gt 1 ]; then
  usage
fi

build_release() {
  stage=$(mktemp -d)
  mkdir -p "$stage/public" "$stage/app"
  cp "$ROOT/index.html" "$stage/public/index.html"
  cp -R "$ROOT/css" "$stage/public/css"
  cp -R "$ROOT/js" "$stage/public/js"
  cp -R "$ROOT/assets" "$stage/public/assets"
  cp "$ROOT/server/brief.py" "$stage/app/brief.py"
  find "$stage" -name '.DS_Store' -delete
  test -f "$stage/public/index.html"
  test -f "$stage/public/css/styles.css"
  test -f "$stage/public/js/main.js"
  test -f "$stage/app/brief.py"
  find "$stage" -name 'test_*.py' -delete
}

if [ "$action" = "dry-run" ]; then
  build_release
  find "$stage" -type f | sed "s|^$stage/||" | sort
  echo "dry-run: built a release, did not upload"
  exit 0
fi

if [ ! -f "$ROOT/deploy/deploy.env" ]; then
  die "copy deploy/deploy.env.example to deploy/deploy.env"
fi
set -a
# shellcheck disable=SC1091
. "$ROOT/deploy/deploy.env"
set +a

: "${DEPLOY_USER:=deploy}"
: "${DEPLOY_SSH_PORT:=4041}"
# nginx and datum-brief.service always use this directory.
if [ "${DEPLOY_PATH:-/var/www/datum}" != "/var/www/datum" ]; then
  die "the release directory is /var/www/datum"
fi
DEPLOY_PATH=/var/www/datum

[ -n "${DEPLOY_HOST:-}" ] || die "set DEPLOY_HOST in deploy/deploy.env"
[ -n "${DEPLOY_SSH_KEY:-}" ] || die "set DEPLOY_SSH_KEY in deploy/deploy.env"
[[ "$DEPLOY_HOST" =~ ^[A-Za-z0-9.-]+$ ]] || die "DEPLOY_HOST must be a hostname or IPv4 address"
[[ "$DEPLOY_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "DEPLOY_USER must be a short unix name"
[ "$DEPLOY_SSH_PORT" = "4041" ] || die "SSH is on port 4041"
[ -f "$DEPLOY_SSH_KEY" ] || die "DEPLOY_SSH_KEY is not a file"
case "$DEPLOY_SSH_KEY" in
  *$'\n'*) die "DEPLOY_SSH_KEY is invalid" ;;
esac

command -v ssh >/dev/null || die "ssh is not installed"
command -v rsync >/dev/null || die "rsync is not installed"

SSH=(
  ssh
  -T
  -p "$DEPLOY_SSH_PORT"
  -i "$DEPLOY_SSH_KEY"
  -o IdentitiesOnly=yes
  -o BatchMode=yes
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=20
)

remote() {
  "${SSH[@]}" "${DEPLOY_USER}@${DEPLOY_HOST}" "$@"
}

if [ "$action" = "rollback" ]; then
  echo "rolling back"
  remote "ACTION=rollback" "DEPLOY_PATH=$DEPLOY_PATH" bash -s < "$ROOT/deploy/switch.sh"
  echo "rolled back"
  exit 0
fi

stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
build_release
echo "release $stamp"
echo "uploading"
rsh=$(printf '%q ' "${SSH[@]}")
remote "mkdir -p ${DEPLOY_PATH}/releases/${stamp}"
rsync -a --delete -e "$rsh" "$stage/" "${DEPLOY_USER}@${DEPLOY_HOST}:${DEPLOY_PATH}/releases/${stamp}/"
echo "switching"
remote "ACTION=switch" "RELEASE_STAMP=$stamp" "DEPLOY_PATH=$DEPLOY_PATH" bash -s < "$ROOT/deploy/switch.sh"
echo "live"
