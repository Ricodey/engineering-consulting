#!/usr/bin/env bash
# Build a release and switch it live, or roll back to the previous one.
# The pod is installed with deploy/raptorpath.sh. The release lives in /home/raptorpath.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
DEPLOY_PATH=/home/raptorpath

die() {
  echo "deploy.sh: $*" >&2
  exit 1
}

usage() {
  echo "usage: ./deploy/deploy.sh [--dry-run|rollback|--install-secret|--print-secret-command|--install-email-secret|--print-email-secret-command]" >&2
  exit 2
}

stage=
token_file=
email_file=
cleanup() {
  if [ -n "$stage" ]; then
    rm -rf "$stage"
  fi
  if [ -n "$token_file" ]; then
    rm -f "$token_file"
  fi
  if [ -n "$email_file" ]; then
    rm -f "$email_file"
  fi
}
trap cleanup EXIT

# These functions run on the server over ssh. They restart only raptorpath units.
# shellcheck disable=SC2317
link_to() {
  rm -rf "$1.next"
  ln -s "$2" "$1.next"
  mv -Tf "$1.next" "$1"
}

# shellcheck disable=SC2317
drop_old_tunnel() {
  systemctl --user disable --now raptorpath-tunnel.service || true
  rm -f "$base/tunnel.env"
  rm -f "$base/form.env" && echo "removed /home/raptorpath/form.env"
  rm -f "$base/.config/containers/systemd/raptorpath-tunnel.container"
  systemctl --user daemon-reload
  systemctl --user reset-failed raptorpath-tunnel.service || true
  podman rm -f raptorpath-tunnel >/dev/null 2>&1 || true
}

# A stopped container still holds the secret, so replace fails until it is gone.
# shellcheck disable=SC2317
drop_cloudflared_container() {
  podman rm -f raptorpath-cloudflared >/dev/null 2>&1 || true
}

# A stopped container still holds the secret, so replace fails until it is gone.
# shellcheck disable=SC2317
drop_brief_container() {
  podman rm -f raptorpath-brief >/dev/null 2>&1 || true
}

# shellcheck disable=SC2317
restart_units() {
  local unit
  for unit in raptorpath-pod raptorpath-web raptorpath-brief raptorpath-cloudflared; do
    case "$unit" in
      raptorpath-*) ;;
      *)
        echo "deploy.sh: refusing unit ${unit}" >&2
        exit 1
        ;;
    esac
  done
  drop_old_tunnel
  systemctl --user stop raptorpath-pod.service raptorpath-web.service raptorpath-brief.service raptorpath-cloudflared.service || true
  drop_cloudflared_container
  drop_brief_container
  systemctl --user daemon-reload
  systemctl --user reset-failed raptorpath-pod.service raptorpath-web.service raptorpath-brief.service raptorpath-cloudflared.service || true
  systemctl --user start raptorpath-pod.service raptorpath-web.service raptorpath-brief.service raptorpath-cloudflared.service
}

# shellcheck disable=SC2317
health_check() {
  local i=0
  while [ "$i" -lt 20 ]; do
    if podman exec raptorpath-brief python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:4174/health", timeout=2).read()' >/dev/null; then
      return 0
    fi
    i=$((i + 1))
    sleep 0.25
  done
  return 1
}

# shellcheck disable=SC2317
restore_failed_cutover() {
  local cur prev
  if [ -f "$base/nginx.conf.previous" ]; then
    mv -f "$base/nginx.conf.previous" "$base/nginx.conf"
    echo "restored nginx.conf"
  fi
  if [ -L "$base/current" ] && [ -L "$base/previous" ]; then
    cur=$(readlink "$base/current")
    prev=$(readlink "$base/previous")
    link_to "$base/current" "$prev"
    link_to "$base/previous" "$cur"
    echo "restored the previous release"
  fi
  restart_units || true
}

# shellcheck disable=SC2317
prune_releases() {
  local keep name
  keep=$(mktemp)
  if [ -L "$base/current" ]; then
    basename "$(readlink "$base/current")" >>"$keep"
  fi
  if [ -L "$base/previous" ]; then
    basename "$(readlink "$base/previous")" >>"$keep"
  fi
  find "$base/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %f\n' |
    sort -nr |
    awk 'NR<=5 {print $2}' >>"$keep"
  find "$base/releases" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' |
    while IFS= read -r name; do
      if ! grep -qx "$name" "$keep"; then
        rm -rf "$base/releases/$name"
      fi
    done
  rm -f "$keep"
}

# shellcheck disable=SC2317
apply_action() {
  local stamp rel old cur prev uid
  base=/home/raptorpath
  case "$base" in
    /home/raptorpath) ;;
    *)
      echo "deploy.sh: refusing path $base" >&2
      exit 1
      ;;
  esac
  [ -d "$base/releases" ] || {
    echo "deploy.sh: $base/releases is missing; run ./deploy/copy.sh" >&2
    exit 1
  }
  uid=$(id -u)
  export XDG_RUNTIME_DIR="/run/user/${uid}"
  if [ -S "$XDG_RUNTIME_DIR/bus" ]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"
  fi
  exec 9>"$base/.deploy.lock"
  flock -w 120 9

  case "${ACTION:?}" in
    drain)
      drop_old_tunnel
      systemctl --user stop raptorpath-pod.service raptorpath-web.service raptorpath-brief.service raptorpath-cloudflared.service || true
      drop_cloudflared_container
      drop_brief_container
      ;;
    switch)
      stamp=${RELEASE_STAMP:?}
      [[ "$stamp" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9]+$ ]] || {
        echo "deploy.sh: bad release id" >&2
        exit 1
      }
      rel="releases/$stamp"
      test -f "$base/$rel/public/index.html"
      test -f "$base/$rel/app/brief.py"
      find "$base/$rel" -type d -exec chmod 755 {} +
      find "$base/$rel" -type f -exec chmod 644 {} +
      sync
      old=$(readlink "$base/current" 2>/dev/null || true)
      link_to "$base/current" "$rel"
      if ! restart_units || ! health_check; then
        if [ -n "$old" ]; then
          link_to "$base/current" "$old"
          restart_units || true
        else
          rm -f "$base/current"
        fi
        echo "deploy.sh: new release failed; restored the previous release" >&2
        exit 1
      fi
      if [ -n "$old" ]; then
        link_to "$base/previous" "$old"
      fi
      prune_releases
      ;;
    rollback)
      if [ ! -L "$base/current" ] || [ ! -L "$base/previous" ]; then
        echo "deploy.sh: no previous release" >&2
        exit 1
      fi
      cur=$(readlink "$base/current")
      prev=$(readlink "$base/previous")
      if [ ! -f "$base/$prev/public/index.html" ] || [ ! -f "$base/$prev/app/brief.py" ]; then
        echo "deploy.sh: previous release is incomplete" >&2
        exit 1
      fi
      link_to "$base/current" "$prev"
      link_to "$base/previous" "$cur"
      if ! restart_units || ! health_check; then
        echo "deploy.sh: rolled back, but the brief service is not healthy" >&2
        exit 1
      fi
      ;;
    cutover-failed)
      restore_failed_cutover
      ;;
    *)
      echo "deploy.sh: unknown action" >&2
      exit 2
      ;;
  esac
}

# shellcheck disable=SC2317
create_tunnel_secret() {
  podman secret create --replace raptorpath-cloudflared -
}

# shellcheck disable=SC2317
create_email_secret() {
  podman secret create --replace raptorpath-email -
}

secret_script() {
  # The remote shell expands these. This shell must not.
  # shellcheck disable=SC2016
  printf '%s\n' \
    'set -euo pipefail' \
    'uid=$(id -u)' \
    'export XDG_RUNTIME_DIR="/run/user/${uid}"' \
    'if [ -S "$XDG_RUNTIME_DIR/bus" ]; then' \
    '  export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"' \
    'fi'
  declare -f create_tunnel_secret
  printf '%s\n' 'create_tunnel_secret'
}

email_secret_script() {
  # The remote shell expands these. This shell must not.
  # shellcheck disable=SC2016
  printf '%s\n' \
    'set -euo pipefail' \
    'uid=$(id -u)' \
    'export XDG_RUNTIME_DIR="/run/user/${uid}"' \
    'if [ -S "$XDG_RUNTIME_DIR/bus" ]; then' \
    '  export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"' \
    'fi'
  declare -f create_email_secret
  printf '%s\n' 'create_email_secret'
}

build_release() {
  stage=$(mktemp -d)
  # shellcheck disable=SC1091
  . "$ROOT/deploy/stage.sh"
  stage_release "$ROOT" "$stage"
}

action=deploy
case "${1:-}" in
  "") ;;
  --dry-run) action=dry-run ;;
  rollback) action=rollback ;;
  --install-secret) action=install-secret ;;
  --print-secret-command) action=print-secret ;;
  --install-email-secret) action=install-email-secret ;;
  --print-email-secret-command) action=print-email-secret ;;
  *) usage ;;
esac
if [ "$#" -gt 1 ]; then
  usage
fi

if [ "$action" = "dry-run" ]; then
  build_release
  find "$stage" -type f | sed "s|^$stage/||" | sort
  echo "dry-run: built a release, did not upload"
  exit 0
fi

if [ "$action" = "print-secret" ]; then
  secret_script
  exit 0
fi

if [ "$action" = "install-secret" ]; then
  bash -c "$(secret_script)"
  exit 0
fi

if [ "$action" = "print-email-secret" ]; then
  email_secret_script
  exit 0
fi

if [ "$action" = "install-email-secret" ]; then
  bash -c "$(email_secret_script)"
  exit 0
fi

if [ ! -f "$ROOT/deploy/deploy.env" ]; then
  die "copy deploy/deploy.env.example to deploy/deploy.env"
fi
set -a
# shellcheck disable=SC1091
. "$ROOT/deploy/deploy.env"
set +a

: "${DEPLOY_USER:=raptorpath}"
: "${DEPLOY_SSH_PORT:=4041}"
if [ "${DEPLOY_PATH:-/home/raptorpath}" != "/home/raptorpath" ]; then
  die "the release directory is /home/raptorpath"
fi
DEPLOY_PATH=/home/raptorpath
[ "$DEPLOY_USER" = "raptorpath" ] || die "DEPLOY_USER must be raptorpath"

[ -n "${DEPLOY_HOST:-}" ] || die "set DEPLOY_HOST in deploy/deploy.env"
[ -n "${DEPLOY_SSH_KEY:-}" ] || die "set DEPLOY_SSH_KEY in deploy/deploy.env"
[[ "$DEPLOY_HOST" =~ ^[A-Za-z0-9.-]+$ ]] || die "DEPLOY_HOST must be a hostname or IPv4 address"
[[ "$DEPLOY_SSH_PORT" =~ ^[0-9]+$ ]] || die "DEPLOY_SSH_PORT must be a TCP port"
port_num=$((10#$DEPLOY_SSH_PORT))
if [ "$port_num" -lt 1 ] || [ "$port_num" -gt 65535 ]; then
  die "DEPLOY_SSH_PORT must be a TCP port"
fi
DEPLOY_SSH_PORT=$port_num
[ -f "$DEPLOY_SSH_KEY" ] || die "DEPLOY_SSH_KEY is not a file"
case "$DEPLOY_SSH_KEY" in
  *$'\n'*) die "DEPLOY_SSH_KEY is invalid" ;;
esac
[ -n "${DEPLOY_SSH_KNOWN_HOSTS:-}" ] || die "set DEPLOY_SSH_KNOWN_HOSTS in deploy/deploy.env"
[ -f "$DEPLOY_SSH_KNOWN_HOSTS" ] || die "DEPLOY_SSH_KNOWN_HOSTS is not a file"

command -v ssh >/dev/null || die "ssh is not installed"
command -v scp >/dev/null || die "scp is not installed"

SSH=(
  ssh
  -T
  -p "$DEPLOY_SSH_PORT"
  -i "$DEPLOY_SSH_KEY"
  -o IdentitiesOnly=yes
  -o BatchMode=yes
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=${DEPLOY_SSH_KNOWN_HOSTS}"
  -o ConnectTimeout=20
)

remote() {
  "${SSH[@]}" "${DEPLOY_USER}@${DEPLOY_HOST}" "$@"
}

SCP=(
  scp
  -r
  -P "$DEPLOY_SSH_PORT"
  -i "$DEPLOY_SSH_KEY"
  -o IdentitiesOnly=yes
  -o BatchMode=yes
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=${DEPLOY_SSH_KNOWN_HOSTS}"
  -o ConnectTimeout=20
)

send_action() {
  {
    echo "set -euo pipefail"
    declare -f link_to drop_old_tunnel drop_cloudflared_container drop_brief_container restart_units health_check prune_releases restore_failed_cutover apply_action
    echo "apply_action"
  } | remote env "ACTION=$1" "RELEASE_STAMP=${2:-}" bash -s
}

install_tunnel_secret() {
  local script quoted
  script=$(secret_script)
  quoted=$(printf '%q' "$script")
  remote "bash -c ${quoted}" <"$token_file"
}

install_email_secret() {
  local script quoted
  script=$(email_secret_script)
  quoted=$(printf '%q' "$script")
  remote "bash -c ${quoted}" <"$email_file"
}

if [ "$action" = "rollback" ]; then
  echo "rolling back"
  send_action rollback
  echo "rolled back"
  exit 0
fi

command -v python3 >/dev/null || die "python3 is not installed"
[ -n "${CLOUDFLARE_API_TOKEN:-}" ] || die "set CLOUDFLARE_API_TOKEN in deploy/deploy.env"
[ -n "${CLOUDFLARE_ACCOUNT_ID:-}" ] || die "set CLOUDFLARE_ACCOUNT_ID in deploy/deploy.env"
[ -n "${CLOUDFLARE_ZONE_ID:-}" ] || die "set CLOUDFLARE_ZONE_ID in deploy/deploy.env"
[ -n "${CF_EMAIL_TOKEN:-}" ] || die "set CF_EMAIL_TOKEN in deploy/deploy.env"
case "$CF_EMAIL_TOKEN" in
  *$'\n'* | *$'\r'*) die "CF_EMAIL_TOKEN is invalid" ;;
esac
email_file=$(mktemp)
chmod 600 "$email_file"
printf '%s' "$CF_EMAIL_TOKEN" >"$email_file"
unset CF_EMAIL_TOKEN

remote "test -d ${DEPLOY_PATH}/releases" || die "$DEPLOY_PATH/releases is missing; run ./deploy/copy.sh"

token_file=$(mktemp)
chmod 600 "$token_file"
echo "preparing the tunnel"
python3 "$ROOT/deploy/cloudflare_tunnel.py" prepare --token-file "$token_file"

stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
build_release
echo "release $stamp"
echo "uploading"
remote "mkdir -p ${DEPLOY_PATH}/releases/${stamp} ${DEPLOY_PATH}/.config/containers/systemd"
"${SCP[@]}" "$stage/public" "$stage/app" "${DEPLOY_USER}@${DEPLOY_HOST}:${DEPLOY_PATH}/releases/${stamp}/"
echo "installing the tunnel unit"
"${SCP[@]}" "$ROOT/deploy/pod/raptorpath-cloudflared.container" \
  "${DEPLOY_USER}@${DEPLOY_HOST}:${DEPLOY_PATH}/.config/containers/systemd/raptorpath-cloudflared.container"
remote "chmod 644 ${DEPLOY_PATH}/.config/containers/systemd/raptorpath-cloudflared.container"
echo "installing the brief unit"
"${SCP[@]}" "$ROOT/deploy/pod/raptorpath-brief.container" \
  "${DEPLOY_USER}@${DEPLOY_HOST}:${DEPLOY_PATH}/.config/containers/systemd/raptorpath-brief.container"
remote "chmod 644 ${DEPLOY_PATH}/.config/containers/systemd/raptorpath-brief.container"
echo "installing nginx.conf"
remote "if [ -f ${DEPLOY_PATH}/nginx.conf ]; then cp -a ${DEPLOY_PATH}/nginx.conf ${DEPLOY_PATH}/nginx.conf.previous; fi"
"${SCP[@]}" "$ROOT/deploy/pod/nginx.conf" \
  "${DEPLOY_USER}@${DEPLOY_HOST}:${DEPLOY_PATH}/nginx.conf"
remote "chmod 644 ${DEPLOY_PATH}/nginx.conf"
echo "draining"
send_action drain
echo "installing the tunnel secret"
install_tunnel_secret
rm -f "$token_file"
token_file=
echo "installing the email secret"
install_email_secret
rm -f "$email_file"
email_file=
echo "installed the email secret"
echo "switching"
send_action switch "$stamp"
echo "cutting over"
if ! python3 "$ROOT/deploy/cloudflare_tunnel.py" finish; then
  echo "cutting over failed; restoring the previous release" >&2
  send_action cutover-failed || true
  exit 1
fi
echo "live"
