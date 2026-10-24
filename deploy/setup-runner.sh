#!/usr/bin/env bash
# Install the GitHub Actions runner for this repository. Run on the server as root,
# after deploy/setup-server.sh. The runner connects outbound to GitHub. It does not
# listen on a port, reconfigure SSH, or change the firewall.
#
# RUNNER_TOKEN is a one-hour registration token from GitHub. Do not commit it.
set -euo pipefail

repo_url=https://github.com/Ricodey/engineering-consulting
repo_slug=Ricodey/engineering-consulting
runner_version=2.338.1
runner_name=datum
runner_label=datum
runner_home=/home/deploy/actions-runner

die() {
  echo "setup-runner.sh: $*" >&2
  exit 1
}

if [ "$(id -u)" -ne 0 ]; then
  die "run this as root"
fi
[ -n "${RUNNER_TOKEN:-}" ] || die "set RUNNER_TOKEN to a registration token from GitHub"
case "$RUNNER_TOKEN" in
  *[[:space:]]*) die "RUNNER_TOKEN is invalid" ;;
esac
id -u deploy >/dev/null 2>&1 || die "user deploy is missing; run deploy/setup-server.sh first"
[ -f /etc/datum/deploy-user ] || die "user deploy is not this app's account; run deploy/setup-server.sh first"
[ -d /home/deploy ] || die "/home/deploy is missing"
[ -d /var/www/datum ] || die "/var/www/datum is missing; run deploy/setup-server.sh first"

case "$(uname -m)" in
  x86_64)
    runner_arch=x64
    runner_sha256=08e9ccb3604d2462c24b5e33604dc30810296ca15cc65f23cffd4317eb144c37
    ;;
  aarch64 | arm64)
    runner_arch=arm64
    runner_sha256=475e0c149fd3b611610127b9664ba5bacf5c6928795cc448ab520bb5f324bede
    ;;
  *)
    die "unsupported architecture $(uname -m)"
    ;;
esac

policy_added=0
workdir=
cleanup() {
  if [ "$policy_added" -eq 1 ]; then
    rm -f /usr/sbin/policy-rc.d
  fi
  if [ -n "$workdir" ]; then
    rm -rf "$workdir"
  fi
}
trap cleanup EXIT

block_service_restarts() {
  policy_added=1
  cat >/usr/sbin/policy-rc.d <<'EOF'
#!/bin/sh
exit 101
EOF
  chmod 755 /usr/sbin/policy-rc.d
}

unblock_service_restarts() {
  policy_added=0
  rm -f /usr/sbin/policy-rc.d
}

# Stop only the unit this runner recorded. Do not touch any other service.
remove_existing_unit() {
  [ -f "$runner_home/.service" ] || return 0
  unit=$(tr -d '[:space:]' <"$runner_home/.service")
  case "$unit" in
    actions.runner.*.service) ;;
    *) die "unexpected runner unit name in $runner_home/.service" ;;
  esac
  systemctl stop "$unit" || true
  systemctl disable "$unit" || true
  rm -f "/etc/systemd/system/$unit"
  rm -f "$runner_home/.service"
  systemctl daemon-reload || true
}

remove_existing_unit

export DEBIAN_FRONTEND=noninteractive
block_service_restarts
apt-get update
apt-get install -y curl ca-certificates tar git openssh-client rsync
unblock_service_restarts

workdir=$(mktemp -d)
archive="$workdir/runner.tar.gz"
package="actions-runner-linux-${runner_arch}-${runner_version}.tar.gz"
url="https://github.com/actions/runner/releases/download/v${runner_version}/${package}"
curl -fsSL --retry 3 --max-time 180 "$url" -o "$archive"
echo "${runner_sha256}  ${archive}" | sha256sum -c -

install -d -m 750 -o deploy -g deploy "$runner_home"
tar -xzf "$archive" -C "$runner_home"
chown -R deploy:deploy "$runner_home"

block_service_restarts
"$runner_home/bin/installdependencies.sh"
unblock_service_restarts

replace_args=()
if [ -f "$runner_home/.runner" ]; then
  replace_args=(--replace)
fi
sudo -u deploy -- "$runner_home/config.sh" \
  --unattended \
  --url "$repo_url" \
  --token "$RUNNER_TOKEN" \
  --name "$runner_name" \
  --labels "$runner_label" \
  --work _work \
  "${replace_args[@]}"

chmod 750 "$runner_home" /home/deploy
for secret_file in .credentials .credentials_rsaparams .runner .path; do
  if [ -f "$runner_home/$secret_file" ]; then
    chown deploy:deploy "$runner_home/$secret_file"
    chmod 600 "$runner_home/$secret_file"
  fi
done

(
  cd "$runner_home"
  ./svc.sh install deploy
  ./svc.sh start
)

echo "runner ${runner_name} is registered for ${repo_slug}"
echo "it runs as user deploy, label ${runner_label}, and listens on no port"
echo "SSH and the firewall were not changed"
echo "publishing a release deploys that tag with deploy/deploy.sh"
