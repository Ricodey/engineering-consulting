#!/usr/bin/env bash
# Create the raptorpath user, install its pod, or remove both.
# bootstrap is the only root command. install runs as raptorpath.
# Bootstrap does not change sshd or another account.
set -euo pipefail

user=raptorpath
home=${RAPTORPATH_HOME:-/home/raptorpath}
marker=${RAPTORPATH_MARKER:-$home/.raptorpath-install}
range_size=65536
reserved_start=100000
reserved_size=65536
subuid_file=${RAPTORPATH_SUBUID_FILE:-/etc/subuid}
subgid_file=${RAPTORPATH_SUBGID_FILE:-/etc/subgid}

die() {
  echo "raptorpath.sh: $*" >&2
  exit 1
}

usage() {
  echo "usage: raptorpath.sh bootstrap|install|remove [--dry-run]" >&2
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
    "$home" | "$home"/*) ;;
    *)
      die "refusing to change $path"
      ;;
  esac
}

accept_public_key() {
  local line seen=0 accepted="" text=${DEPLOY_AUTHORIZED_KEY:-}
  [ -n "$text" ] || die "set DEPLOY_AUTHORIZED_KEY to the raptorpath public key"
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%$'\r'}
    [ -z "$line" ] && continue
    case "$line" in
      \#*) continue ;;
      ssh-ed25519\ * | ssh-rsa\ * | ecdsa-sha2-nistp256\ * | ecdsa-sha2-nistp384\ * | ecdsa-sha2-nistp521\ *)
        accepted=$line
        seen=$((seen + 1))
        ;;
      *)
        die "DEPLOY_AUTHORIZED_KEY has a line that is not an OpenSSH public key"
        ;;
    esac
  done <<<"$text"
  [ "$seen" -eq 1 ] || die "DEPLOY_AUTHORIZED_KEY must contain one public key"
  printf '%s\n' "$accepted"
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

# Quadlets need Podman 4.4 or newer. An existing podman is left alone so a
# shared host does not get an upgrade. apt runs only when the command is missing.
ensure_podman() {
  if command -v podman >/dev/null 2>&1; then
    echo "podman already installed"
    return 0
  fi
  command -v apt-get >/dev/null 2>&1 || die "podman is not installed and apt-get is not available"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y podman uidmap
  command -v podman >/dev/null 2>&1 || die "podman is not installed"
  echo "installed podman"
}

require_podman() {
  local version major minor
  command -v podman >/dev/null 2>&1 || die "podman is not installed"
  version=$(podman --version 2>/dev/null) || die "podman is not installed"
  version=${version##* }
  major=${version%%.*}
  minor=${version#*.}
  minor=${minor%%.*}
  [[ "$major" =~ ^[0-9]+$ && "$minor" =~ ^[0-9]+$ ]] || die "could not read the podman version"
  if [ "$major" -lt 4 ]; then
    die "podman ${version} is too old; quadlets need 4.4 or newer"
  fi
  if [ "$major" -eq 4 ] && [ "$minor" -lt 4 ]; then
    die "podman ${version} is too old; quadlets need 4.4 or newer"
  fi
}

bootstrap() {
  local start dry=$1 key_line key_file
  load_ranges
  start=$(next_free_start)
  assert_range_clear "$start"
  printf 'user=%s\n' "$user"
  printf 'home=%s\n' "$home"
  printf 'subuid_start=%s\n' "$start"
  printf 'subuid_size=%s\n' "$range_size"
  [ "$dry" -eq 1 ] && return 0

  key_line=$(accept_public_key)
  [ "$(id -u)" -eq 0 ] || die "run bootstrap as root"
  refuse_foreign_path "$home"
  ensure_podman
  require_podman
  if id -u "$user" >/dev/null 2>&1; then
    [ -f "$marker" ] || die "user raptorpath already exists and is not this install"
    echo "raptorpath already installed"
    return 0
  fi
  useradd --create-home --shell /bin/bash --home-dir "$home" "$user"
  rewrite_id_file "$subuid_file" "$start"
  rewrite_id_file "$subgid_file" "$start"
  chmod 750 "$home"
  install -d -m 700 -o "$user" -g "$user" "$home/.ssh"
  key_file=$(mktemp)
  chmod 600 "$key_file"
  printf '%s\n' "$key_line" >"$key_file"
  install -m 600 -o "$user" -g "$user" "$key_file" "$home/.ssh/authorized_keys"
  rm -f "$key_file"
  touch "$marker"
  chown "$user:$user" "$marker"
  chmod 644 "$marker"
  passwd -l "$user" >/dev/null
  loginctl enable-linger "$user"
  echo "bootstrapped $user"
}

secrets_present() {
  podman secret inspect raptorpath-email >/dev/null 2>&1 &&
    podman secret inspect raptorpath-cloudflared >/dev/null 2>&1
}

install_pod() {
  local incoming=$home/incoming
  [ "$(id -un)" = "$user" ] || die "run install as raptorpath"
  if ! command -v podman >/dev/null 2>&1; then
    die "podman is not installed; run raptorpath.sh bootstrap as root"
  fi
  require_podman
  [ -d "$incoming" ] || die "$incoming is missing; run deploy/copy.sh"
  [ -f "$incoming/pod/raptorpath.pod" ] || die "pod templates are missing"
  [ -f "$incoming/pod/nginx.conf" ] || die "nginx.conf is missing"
  [ -f "$incoming/public/index.html" ] || die "public/index.html is missing"
  [ -f "$incoming/app/brief.py" ] || die "app/brief.py is missing"
  refuse_foreign_path "$home"
  refuse_foreign_path "$incoming"

  install -d -m 755 "$home/releases" "$home/.config/containers/systemd"
  rm -f "$home/form.env"
  install -m 644 "$incoming/pod/nginx.conf" "$home/nginx.conf"
  install -m 644 "$incoming/pod/raptorpath.pod" "$home/.config/containers/systemd/raptorpath.pod"
  install -m 644 "$incoming/pod/raptorpath-web.container" "$home/.config/containers/systemd/raptorpath-web.container"
  install -m 644 "$incoming/pod/raptorpath-brief.container" "$home/.config/containers/systemd/raptorpath-brief.container"
  if [ ! -e "$home/current" ]; then
    install -d -m 755 "$home/releases/initial"
    ln -s releases/initial "$home/current"
  fi
  if [ ! -f "$home/current/app/brief.py" ]; then
    rm -rf "$home/current/public" "$home/current/app"
    cp -a "$incoming/public" "$home/current/public"
    cp -a "$incoming/app" "$home/current/app"
    find -H "$home/current/public" "$home/current/app" -type d -exec chmod 755 {} +
    find -H "$home/current/public" "$home/current/app" -type f -exec chmod 644 {} +
  fi
  [ -f "$home/current/app/brief.py" ] || die "current release has no app/brief.py"
  [ -f "$home/current/public/index.html" ] || die "current release has no index.html"
  rm -rf "$incoming"

  user_session
  systemctl --user daemon-reload
  if ! secrets_present; then
    echo "the first release will start the units"
    return 0
  fi
  systemctl --user reset-failed raptorpath-pod.service raptorpath-web.service raptorpath-brief.service
  systemctl --user restart raptorpath-pod.service raptorpath-web.service raptorpath-brief.service
  echo "installed the raptorpath pod"
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

remove_site() {
  local dry=$1
  local owner
  refuse_foreign_path "$home"
  printf 'user=%s\n' "$user"
  printf 'home=%s\n' "$home"
  [ "$dry" -eq 1 ] && return 0

  [ "$(id -u)" -eq 0 ] || die "run remove as root"
  if [ ! -d "$home" ] && ! id -u "$user" >/dev/null 2>&1; then
    echo "raptorpath is not installed"
    return 0
  fi
  [ -f "$marker" ] || die "user raptorpath is not this install"
  owner=$(stat -c '%U' "$home")
  [ "$owner" = "$user" ] || die "$home is owned by $owner"
  systemctl --machine="${user}@" --user stop raptorpath-web.service raptorpath-brief.service raptorpath-cloudflared.service raptorpath-tunnel.service raptorpath-pod.service || true
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
  remove) remove_site "$dry" ;;
  *) usage ;;
esac
