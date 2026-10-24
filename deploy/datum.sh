#!/usr/bin/env bash
# Create the datum user, install its pod, or remove both.
# bootstrap is the only root command. install runs as datum.
# Nothing here changes sshd, the firewall, or another account.
set -euo pipefail

user=datum
home=/home/datum
marker=$home/.datum-install
range_size=65536
reserved_start=100000
reserved_size=65536
subuid_file=${DATUM_SUBUID_FILE:-/etc/subuid}
subgid_file=${DATUM_SUBGID_FILE:-/etc/subgid}

die() {
  echo "datum.sh: $*" >&2
  exit 1
}

usage() {
  echo "usage: datum.sh bootstrap|install|remove [--dry-run]" >&2
  exit 2
}

ranges_overlap() {
  local a as b bs ae be
  a=$1
  as=$2
  b=$3
  bs=$4
  ae=$((a + as))
  be=$((b + bs))
  [ "$a" -lt "$be" ] && [ "$b" -lt "$ae" ]
}

load_ranges() {
  ranges=("$reserved_start $reserved_size")
  local file name start size
  for file in "$subuid_file" "$subgid_file"; do
    [ -f "$file" ] || continue
    while IFS=: read -r name start size; do
      [ -n "$name" ] || continue
      case "$name" in
        \#*) continue ;;
      esac
      [[ "$start" =~ ^[0-9]+$ ]] || die "bad range in $file"
      [[ "$size" =~ ^[0-9]+$ ]] || die "bad range in $file"
      ranges+=("$start $size")
    done <"$file"
  done
}

next_free_start() {
  local start=65536
  local guard=0
  local hit b bs
  while [ "$guard" -lt 10000 ]; do
    hit=0
    for pair in "${ranges[@]}"; do
      # shellcheck disable=SC2086
      set -- $pair
      b=$1
      bs=$2
      if ranges_overlap "$start" "$range_size" "$b" "$bs"; then
        start=$((b + bs))
        hit=1
        break
      fi
    done
    [ "$hit" -eq 0 ] && break
    guard=$((guard + 1))
  done
  [ "$guard" -lt 10000 ] || die "no free subuid range"
  printf '%s\n' "$start"
}

assert_range_clear() {
  local start=$1
  local b bs
  for pair in "${ranges[@]}"; do
    # shellcheck disable=SC2086
    set -- $pair
    b=$1
    bs=$2
    if ranges_overlap "$start" "$range_size" "$b" "$bs"; then
      die "subuid range overlaps an existing account"
    fi
  done
}

refuse_foreign_path() {
  local path=$1
  case "$path" in
    /home/pancake | /home/pancake/* | /etc/pancake | /etc/pancake/*)
      die "refusing to change $path"
      ;;
    /home/datum | /home/datum/*) ;;
    *)
      die "refusing to change $path"
      ;;
  esac
}

require_public_key() {
  local file=$1
  local seen=0
  local line
  [ -n "$file" ] || die "set DEPLOY_AUTHORIZED_KEY_FILE to the datum public key"
  [ -f "$file" ] || die "DEPLOY_AUTHORIZED_KEY_FILE is not a file"
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%$'\r'}
    [ -z "$line" ] && continue
    case "$line" in
      \#*) continue ;;
      ssh-ed25519\ * | ssh-rsa\ * | ecdsa-sha2-nistp256\ * | ecdsa-sha2-nistp384\ * | ecdsa-sha2-nistp521\ *)
        seen=$((seen + 1))
        ;;
      *)
        die "DEPLOY_AUTHORIZED_KEY_FILE has a line that is not an OpenSSH public key"
        ;;
    esac
  done <"$file"
  [ "$seen" -eq 1 ] || die "DEPLOY_AUTHORIZED_KEY_FILE must contain one public key"
}

rewrite_id_file() {
  local file=$1
  local start=$2
  local tmp
  tmp=$(mktemp)
  if [ -f "$file" ]; then
    awk -F: -v name="$user" '$1 != name { print }' "$file" >"$tmp"
  fi
  printf '%s:%s:%s\n' "$user" "$start" "$range_size" >>"$tmp"
  install -m 644 "$tmp" "$file"
  rm -f "$tmp"
}

user_session() {
  local uid
  uid=$(id -u)
  export XDG_RUNTIME_DIR="/run/user/${uid}"
  if [ -S "$XDG_RUNTIME_DIR/bus" ]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"
  fi
}

bootstrap() {
  local start dry=$1
  load_ranges
  start=$(next_free_start)
  assert_range_clear "$start"
  printf 'user=%s\n' "$user"
  printf 'home=%s\n' "$home"
  printf 'subuid_start=%s\n' "$start"
  printf 'subuid_size=%s\n' "$range_size"
  [ "$dry" -eq 1 ] && return 0

  [ "$(id -u)" -eq 0 ] || die "run bootstrap as root"
  require_public_key "${DEPLOY_AUTHORIZED_KEY_FILE:-}"
  refuse_foreign_path "$home"
  if id -u "$user" >/dev/null 2>&1; then
    [ -f "$marker" ] || die "user datum already exists and is not this install"
    echo "datum already installed"
    return 0
  fi
  useradd --create-home --shell /bin/bash --home-dir "$home" "$user"
  rewrite_id_file "$subuid_file" "$start"
  rewrite_id_file "$subgid_file" "$start"
  chmod 750 "$home"
  install -d -m 700 -o "$user" -g "$user" "$home/.ssh"
  install -m 600 -o "$user" -g "$user" "$DEPLOY_AUTHORIZED_KEY_FILE" "$home/.ssh/authorized_keys"
  touch "$marker"
  chown "$user:$user" "$marker"
  chmod 644 "$marker"
  passwd -l "$user" >/dev/null
  loginctl enable-linger "$user"
  echo "bootstrapped $user"
}

install_pod() {
  local incoming=$home/incoming
  [ "$(id -un)" = "$user" ] || die "run install as datum"
  [ -d "$incoming" ] || die "$incoming is missing; run deploy/copy.sh"
  [ -f "$incoming/form.env" ] || die "$incoming/form.env is missing"
  [ -s "$incoming/form.env" ] || die "$incoming/form.env is empty"
  [ -f "$incoming/pod/datum.pod" ] || die "pod templates are missing"
  [ -f "$incoming/pod/nginx.conf" ] || die "nginx.conf is missing"
  refuse_foreign_path "$home"
  refuse_foreign_path "$incoming"

  install -d -m 755 "$home/releases" "$home/.config/containers/systemd"
  install -m 600 "$incoming/form.env" "$home/form.env"
  install -m 644 "$incoming/pod/nginx.conf" "$home/nginx.conf"
  install -m 644 "$incoming/pod/datum.pod" "$home/.config/containers/systemd/datum.pod"
  install -m 644 "$incoming/pod/datum-web.container" "$home/.config/containers/systemd/datum-web.container"
  install -m 644 "$incoming/pod/datum-brief.container" "$home/.config/containers/systemd/datum-brief.container"
  if [ ! -e "$home/current" ]; then
    install -d -m 755 "$home/releases/initial/public" "$home/releases/initial/app"
    ln -s releases/initial "$home/current"
  fi
  rm -rf "$incoming"

  user_session
  systemctl --user daemon-reload
  systemctl --user start datum-pod.service datum-web.service datum-brief.service
  echo "installed the datum pod"
}

drop_id_lines() {
  local file=$1
  local tmp
  [ -f "$file" ] || return 0
  tmp=$(mktemp)
  awk -F: -v name="$user" '$1 != name { print }' "$file" >"$tmp"
  install -m 644 "$tmp" "$file"
  rm -f "$tmp"
}

remove_datum() {
  local dry=$1
  local owner
  refuse_foreign_path "$home"
  printf 'user=%s\n' "$user"
  printf 'home=%s\n' "$home"
  [ "$dry" -eq 1 ] && return 0

  [ "$(id -u)" -eq 0 ] || die "run remove as root"
  if [ ! -d "$home" ] && ! id -u "$user" >/dev/null 2>&1; then
    echo "datum is not installed"
    return 0
  fi
  [ -f "$marker" ] || die "user datum is not this install"
  owner=$(stat -c '%U' "$home")
  [ "$owner" = "$user" ] || die "$home is owned by $owner"
  systemctl --machine="${user}@" --user stop datum-web.service datum-brief.service datum-pod.service || true
  loginctl disable-linger "$user" || true
  userdel -r "$user"
  drop_id_lines "$subuid_file"
  drop_id_lines "$subgid_file"
  echo "removed $user"
}

command=${1:-}
dry=0
case "${2:-}" in
  "") ;;
  --dry-run) dry=1 ;;
  *) usage ;;
esac
if [ "$#" -gt 2 ]; then
  usage
fi

case "$command" in
  bootstrap) bootstrap "$dry" ;;
  install)
    [ "$dry" -eq 0 ] || die "install has no dry run"
    install_pod
    ;;
  remove) remove_datum "$dry" ;;
  *) usage ;;
esac
