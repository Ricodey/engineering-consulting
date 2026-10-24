#!/usr/bin/env bash
# Idempotent host setup for the Datum site. Run on the server as root.
# Debian 12 or Ubuntu 22.04+, SSH on port 22.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)

die() {
  echo "setup-server.sh: $*" >&2
  exit 1
}

if [ "$(id -u)" -ne 0 ]; then
  die "run this as root"
fi

[ -n "${SITE_DOMAIN:-}" ] || die "set SITE_DOMAIN to the hostname nginx should serve"
[ -n "${DEPLOY_AUTHORIZED_KEY_FILE:-}" ] || die "set DEPLOY_AUTHORIZED_KEY_FILE to the deploy user's public key"
[[ "$SITE_DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$ ]] \
  || die "SITE_DOMAIN must be a hostname such as datum.studio (use punycode for IDN)"
[ -f "$DEPLOY_AUTHORIZED_KEY_FILE" ] || die "DEPLOY_AUTHORIZED_KEY_FILE is not a file"

seen=0
while IFS= read -r line || [ -n "$line" ]; do
  line=${line%$'\r'}
  [ -z "$line" ] && continue
  case "$line" in
    \#*) continue ;;
    ssh-ed25519\ *|ssh-rsa\ *|ecdsa-sha2-nistp256\ *|ecdsa-sha2-nistp384\ *|ecdsa-sha2-nistp521\ *)
      seen=1
      ;;
    *)
      die "DEPLOY_AUTHORIZED_KEY_FILE has a line that is not an OpenSSH public key"
      ;;
  esac
done < "$DEPLOY_AUTHORIZED_KEY_FILE"
[ "$seen" -eq 1 ] || die "DEPLOY_AUTHORIZED_KEY_FILE is empty"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y nginx python3 ufw sudo curl ca-certificates

[ -x /usr/bin/python3 ] || die "python3 is not at /usr/bin/python3"
[ -x /usr/bin/systemctl ] || die "systemctl is not at /usr/bin/systemctl"
[ -x /usr/sbin/nginx ] || die "nginx is not at /usr/sbin/nginx"
command -v flock >/dev/null || die "flock is missing"
command -v curl >/dev/null || die "curl is missing"
grep -F -q 'include /etc/nginx/mime.types;' /etc/nginx/nginx.conf \
  || die "nginx.conf does not include mime.types"
grep -F -q 'include /etc/nginx/conf.d/*.conf;' /etc/nginx/nginx.conf \
  || die "nginx.conf does not include conf.d"
grep -F -q 'include /etc/nginx/sites-enabled/*;' /etc/nginx/nginx.conf \
  || die "nginx.conf does not include sites-enabled"

if ! id -u deploy >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash --home-dir /home/deploy deploy
fi
if ! id -u datum >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin datum
fi
passwd -l deploy >/dev/null

install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
install -m 600 -o deploy -g deploy "$DEPLOY_AUTHORIZED_KEY_FILE" /home/deploy/.ssh/authorized_keys

install -d -m 755 -o deploy -g deploy /var/www/datum
install -d -m 755 -o deploy -g deploy /var/www/datum/releases
install -d -m 755 -o root -g root /etc/datum
install -d -m 755 -o root -g root /etc/ssl/cloudflare
install -d -m 755 -o root -g root /etc/nginx/snippets

if [ ! -s /etc/ssl/cloudflare/origin.pem ] || [ ! -s /etc/ssl/cloudflare/origin.key ]; then
  die "place the Cloudflare origin certificate at /etc/ssl/cloudflare/origin.pem and the private key at /etc/ssl/cloudflare/origin.key"
fi
chown root:root /etc/ssl/cloudflare/origin.pem /etc/ssl/cloudflare/origin.key
chmod 644 /etc/ssl/cloudflare/origin.pem
chmod 600 /etc/ssl/cloudflare/origin.key

if [ ! -f /etc/datum/form.env ]; then
  install -m 600 -o root -g root "$here/form.env.example" /etc/datum/form.env
  echo "created /etc/datum/form.env"
else
  chown root:root /etc/datum/form.env
  chmod 600 /etc/datum/form.env
fi

sudoers=$(mktemp)
cat > "$sudoers" <<'EOF'
deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart datum-brief.service
EOF
visudo -cf "$sudoers" >/dev/null
install -m 440 -o root -g root "$sudoers" /etc/sudoers.d/datum-deploy
rm -f "$sudoers"

ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw default deny incoming
ufw default allow outgoing
ufw --force enable

is_cidr() {
  case "$1" in
    0.0.0.0/0|::/0) return 1 ;;
  esac
  [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}$ ]] && return 0
  [[ "$1" =~ ^[0-9A-Fa-f:]+/[0-9]{1,3}$ ]] && return 0
  return 1
}

fetch_cidrs() {
  url=$1
  dest=$2
  curl -fsS --max-time 30 "$url" -o "$dest"
  count=0
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%$'\r'}
    [ -z "$line" ] && continue
    is_cidr "$line" || die "unexpected line from $url"
    count=$((count + 1))
  done < "$dest"
  [ "$count" -ge 4 ] || die "$url returned too few addresses"
}

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fetch_cidrs https://www.cloudflare.com/ips-v4 "$tmp/v4"
fetch_cidrs https://www.cloudflare.com/ips-v6 "$tmp/v6"

{
  echo 'geo $remote_addr $is_cloudflare {'
  echo '    default 0;'
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%$'\r'}
    [ -z "$line" ] && continue
    printf '    %s 1;\n' "$line"
  done < "$tmp/v4"
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%$'\r'}
    [ -z "$line" ] && continue
    printf '    %s 1;\n' "$line"
  done < "$tmp/v6"
  echo '}'
  echo
  echo 'map $http_cf_connecting_ip $brief_ip {'
  echo '    ""      $remote_addr;'
  echo '    default $http_cf_connecting_ip;'
  echo '}'
} > "$tmp/datum-cloudflare.conf"

template=$(<"$here/nginx-site.conf")
printf '%s\n' "${template//__SITE_DOMAIN__/$SITE_DOMAIN}" > "$tmp/datum"

backup=$(mktemp -d)
trap 'rm -rf "$tmp" "$backup"' EXIT
[ -f /etc/nginx/sites-available/datum ] && cp -a /etc/nginx/sites-available/datum "$backup/datum"
[ -f /etc/nginx/conf.d/datum-cloudflare.conf ] && cp -a /etc/nginx/conf.d/datum-cloudflare.conf "$backup/datum-cloudflare.conf"

install -m 644 "$tmp/datum-cloudflare.conf" /etc/nginx/conf.d/datum-cloudflare.conf
install -m 644 "$tmp/datum" /etc/nginx/sites-available/datum
ln -sfn /etc/nginx/sites-available/datum /etc/nginx/sites-enabled/datum

restore_nginx() {
  if [ -f "$backup/datum" ]; then
    cp -a "$backup/datum" /etc/nginx/sites-available/datum
  else
    rm -f /etc/nginx/sites-available/datum /etc/nginx/sites-enabled/datum
  fi
  if [ -f "$backup/datum-cloudflare.conf" ]; then
    cp -a "$backup/datum-cloudflare.conf" /etc/nginx/conf.d/datum-cloudflare.conf
  else
    rm -f /etc/nginx/conf.d/datum-cloudflare.conf
  fi
}

if ! nginx -t; then
  restore_nginx
  die "nginx rejected the config; the previous files were restored"
fi

rm -f /etc/nginx/sites-enabled/default
if ! nginx -t; then
  ln -sfn /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default
  restore_nginx
  die "nginx rejected the config after disabling the default site"
fi

systemctl enable nginx
if systemctl is-active --quiet nginx; then
  systemctl reload nginx
else
  systemctl restart nginx
fi

install -m 644 "$here/datum-brief.service" /etc/systemd/system/datum-brief.service
systemctl daemon-reload
systemctl enable datum-brief.service
if [ -f /var/www/datum/current/app/brief.py ]; then
  systemctl restart datum-brief.service
fi

echo "setup complete for $SITE_DOMAIN"
if ! grep -q '^SMTP_HOST=.\+' /etc/datum/form.env; then
  echo "edit /etc/datum/form.env, then run ./deploy/deploy.sh from your workstation"
fi
echo "DNS is not managed here. Proxy the hostname at Cloudflare and set SSL to Full (strict)."
echo "re-run this script when you want a fresh Cloudflare address list"
