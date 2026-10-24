#!/usr/bin/env bash
# Build the tree a release and the first install both ship.
# Sourced by deploy.sh and copy.sh. A direct run does nothing.
stage_release() {
  local root=$1 dest=$2
  mkdir -p "$dest/public" "$dest/app"
  cp "$root/index.html" "$dest/public/index.html"
  cp -R "$root/css" "$dest/public/css"
  cp -R "$root/js" "$dest/public/js"
  cp -R "$root/assets" "$dest/public/assets"
  cp "$root/server/brief.py" "$dest/app/brief.py"
  find "$dest" -name '.DS_Store' -delete
  find "$dest" -name 'test_*.py' -delete
  test -f "$dest/public/index.html"
  test -f "$dest/public/css/styles.css"
  test -f "$dest/public/js/main.js"
  test -f "$dest/app/brief.py"
}
