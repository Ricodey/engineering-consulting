#!/usr/bin/env bash
# Switch the live release. deploy.sh runs this on the server as the deploy user.
set -euo pipefail

base=${DEPLOY_PATH:?}
action=${ACTION:?}

if [ ! -d "$base/releases" ]; then
  echo "switch.sh: $base/releases is missing; run setup-server.sh" >&2
  exit 1
fi

exec 9>"$base/.deploy.lock"
flock -w 120 9

link_to() {
  rm -rf "$1.next"
  ln -s "$2" "$1.next"
  # -T replaces the symlink itself. Without it, mv follows current into the old release.
  mv -Tf "$1.next" "$1"
}

restart() {
  sudo /usr/bin/systemctl restart datum-brief.service
}

health() {
  i=0
  while [ "$i" -lt 20 ]; do
    if curl -fsS --max-time 2 http://127.0.0.1:4174/health >/dev/null; then
      return 0
    fi
    i=$((i + 1))
    sleep 0.25
  done
  return 1
}

prune() {
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

case "$action" in
  switch)
    stamp=${RELEASE_STAMP:?}
    [[ "$stamp" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9]+$ ]] || {
      echo "switch.sh: bad release id" >&2
      exit 1
    }
    rel="releases/$stamp"
    test -f "$base/$rel/public/index.html"
    test -f "$base/$rel/app/brief.py"
    python3 -c 'import ast,sys; ast.parse(open(sys.argv[1], encoding="utf-8").read())' \
      "$base/$rel/app/brief.py"
    find "$base/$rel" -type d -exec chmod 755 {} +
    find "$base/$rel" -type f -exec chmod 644 {} +
    sync
    old=$(readlink "$base/current" 2>/dev/null || true)
    link_to "$base/current" "$rel"
    if ! restart || ! health; then
      if [ -n "$old" ]; then
        link_to "$base/current" "$old"
        restart || true
      else
        rm -f "$base/current"
      fi
      echo "switch.sh: new release failed; restored the previous release" >&2
      exit 1
    fi
    if [ -n "$old" ]; then
      link_to "$base/previous" "$old"
    fi
    prune
    ;;
  rollback)
    if [ ! -L "$base/current" ] || [ ! -L "$base/previous" ]; then
      echo "switch.sh: no previous release" >&2
      exit 1
    fi
    cur=$(readlink "$base/current")
    prev=$(readlink "$base/previous")
    if [ ! -f "$base/$prev/public/index.html" ] || [ ! -f "$base/$prev/app/brief.py" ]; then
      echo "switch.sh: previous release is incomplete" >&2
      exit 1
    fi
    link_to "$base/current" "$prev"
    link_to "$base/previous" "$cur"
    if ! restart || ! health; then
      echo "switch.sh: rolled back, but the brief service is not healthy" >&2
      exit 1
    fi
    ;;
  *)
    echo "switch.sh: unknown action" >&2
    exit 2
    ;;
esac
