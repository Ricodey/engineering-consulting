#!/usr/bin/env bash
# Copy the pod bundle to the datum account and install it. Run this after bootstrap.
# The SSH user is datum. This script does not log in as any other account.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
defaults=${COPY_DEFAULTS:-$root/deploy/copy.defaults}
dest=/home/datum/incoming
bootstrap_url=https://raw.githubusercontent.com/Ricodey/engineering-consulting/main/deploy/datum.sh

die() {
  echo "copy.sh: $*" >&2
  exit 1
}

usage() {
  echo "usage: ./deploy/copy.sh [--print-plan]" >&2
  exit 2
}

hint_bootstrap() {
  local pub_file pub
  echo "copy.sh: could not log in as datum." >&2
  echo "On the server, as root, once:" >&2
  echo "  curl -fsSL -o /tmp/datum.sh ${bootstrap_url}" >&2
  pub_file="${key}.pub"
  pub=""
  if [ -f "$pub_file" ]; then
    IFS= read -r pub <"$pub_file" || true
    pub=${pub%$'\r'}
  fi
  case "$pub" in
    ssh-ed25519\ * | ssh-rsa\ * | ecdsa-sha2-nistp256\ * | ecdsa-sha2-nistp384\ * | ecdsa-sha2-nistp521\ *)
      pub=${pub//\'/\'\\\'\'}
      echo "  DEPLOY_AUTHORIZED_KEY='${pub}' bash /tmp/datum.sh bootstrap" >&2
      ;;
    *)
      echo "  DEPLOY_AUTHORIZED_KEY='ssh-ed25519 AAAA... datum-deploy' bash /tmp/datum.sh bootstrap" >&2
      ;;
  esac
  echo "  rm -f /tmp/datum.sh" >&2
}

default_value() {
  local key=$1
  [ -f "$defaults" ] || return 0
  awk -F= -v key="$key" '
    /^[ \t]*($|#)/ { next }
    {
      name = $1
      gsub(/^[ \t]+|[ \t]+$/, "", name)
      if (name == key) {
        sub(/^[^=]*=/, "")
        gsub(/^[ \t]+|[ \t]+$/, "")
        print
        exit
      }
    }
  ' "$defaults"
}

action=copy
case "${1:-}" in
  "") ;;
  --print-plan) action=plan ;;
  *) usage ;;
esac
if [ "$#" -gt 1 ]; then
  usage
fi

host=$(default_value HOST)
port=$(default_value SSH_PORT)
ssh_user=$(default_value SSH_USER)
key=$(default_value SSH_KEY)
known=$(default_value KNOWN_HOSTS)
form=$(default_value FORM_ENV)
tunnel=$(default_value TUNNEL_ENV)

[ -n "$host" ] || die "set HOST in $defaults"
[ -n "$port" ] || die "set SSH_PORT in $defaults"
[ "$ssh_user" = "datum" ] || die "SSH_USER must be datum"
[[ "$port" =~ ^[0-9]+$ ]] || die "SSH_PORT must be a TCP port"
[[ "$host" =~ ^[A-Za-z0-9.-]+$ ]] || die "HOST must be a hostname or IPv4 address"

printf 'ssh_user=%s\n' "$ssh_user"
printf 'dest=%s\n' "$dest"
printf 'host=%s\n' "$host"
printf 'port=%s\n' "$port"

if [ "$action" = "plan" ]; then
  exit 0
fi

[ -n "$key" ] || die "set SSH_KEY in $defaults"
[ -f "$key" ] || die "SSH_KEY is not a file"
[ -n "$known" ] || die "set KNOWN_HOSTS in $defaults"
[ -f "$known" ] || die "KNOWN_HOSTS is not a file"
[ -n "$form" ] || die "set FORM_ENV in $defaults"
[ -s "$form" ] || die "FORM_ENV is missing or empty"
[ -n "$tunnel" ] || die "set TUNNEL_ENV in $defaults"
[ -s "$tunnel" ] || die "TUNNEL_ENV is missing or empty"
[ -f "$root/deploy/datum.sh" ] || die "datum.sh is missing"
[ -f "$root/deploy/pod/datum.pod" ] || die "pod templates are missing"
[ -f "$root/deploy/pod/datum-tunnel.container" ] || die "datum-tunnel.container is missing"
[ -f "$root/deploy/pod/nginx.conf" ] || die "nginx.conf is missing"
[ -f "$root/server/brief.py" ] || die "brief.py is missing"
[ -f "$root/index.html" ] || die "index.html is missing"

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
# shellcheck disable=SC1091
. "$root/deploy/stage.sh"
stage_release "$root" "$stage"

ssh=(
  ssh
  -T
  -p "$port"
  -i "$key"
  -o IdentitiesOnly=yes
  -o BatchMode=yes
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=${known}"
  -o ConnectTimeout=20
)
target="${ssh_user}@${host}"

if ! "${ssh[@]}" "$target" "mkdir -p '$dest/pod'"; then
  hint_bootstrap
  exit 1
fi

scp=(
  scp
  -r
  -P "$port"
  -i "$key"
  -o IdentitiesOnly=yes
  -o BatchMode=yes
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=${known}"
  -o ConnectTimeout=20
)
"${scp[@]}" "$root/deploy/datum.sh" "${target}:${dest}/datum.sh"
"${scp[@]}" "$root/deploy/pod/datum.pod" "${target}:${dest}/pod/datum.pod"
"${scp[@]}" "$root/deploy/pod/datum-web.container" "${target}:${dest}/pod/datum-web.container"
"${scp[@]}" "$root/deploy/pod/datum-brief.container" "${target}:${dest}/pod/datum-brief.container"
"${scp[@]}" "$root/deploy/pod/datum-tunnel.container" "${target}:${dest}/pod/datum-tunnel.container"
"${scp[@]}" "$root/deploy/pod/nginx.conf" "${target}:${dest}/pod/nginx.conf"
"${scp[@]}" "$form" "${target}:${dest}/form.env"
"${scp[@]}" "$tunnel" "${target}:${dest}/tunnel.env"
"${scp[@]}" "$stage/public" "$stage/app" "${target}:${dest}/"
"${ssh[@]}" "$target" "bash ${dest}/datum.sh install"
echo "installed the datum pod on ${target}"
