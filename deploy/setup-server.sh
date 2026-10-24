#!/usr/bin/env bash
# Idempotent host setup for the Datum site. Run on the server as root.
# Debian 12 or Ubuntu 22.04+. HTTPS only, behind Cloudflare.
#
# SITE_PREFIX=/ (the default) is a dedicated host: nginx listens on 443,
# sshd listens on 4041, and the firewall allows 4041 and 443.
# Any other prefix is a shared host: nginx listens on 8443 with the origin
# certificate. sshd and the firewall are left alone, and nothing binds 80 or 443.
# Cloudflare sends only that prefix to 8443 (an origin rule). 8443 is a port
# Cloudflare's proxy can use for HTTPS.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
# Shared hosts do not take 443 or 80. Cloudflare can proxy HTTPS on 8443.
shared_port=8443

die() {
  echo "setup-server.sh: $*" >&2
  exit 1
}

usage() {
  echo "usage: SITE_DOMAIN=example.com [SITE_PREFIX=/] bash deploy/setup-server.sh [--print-nginx|--print-plan]" >&2
  exit 2
}

SITE_PREFIX=${SITE_PREFIX:-/}
if [ "$SITE_PREFIX" = "/" ]; then
  host_mode=dedicated
  listen_port=443
elif [[ "$SITE_PREFIX" =~ ^/[A-Za-z0-9][A-Za-z0-9_-]{0,62}$ ]]; then
  host_mode=shared
  listen_port=$shared_port
else
  die "SITE_PREFIX must be / or a single path segment such as /consulting"
fi

mode=install
case "${1:-}" in
  "") ;;
  --print-nginx) mode=print-nginx ;;
  --print-plan) mode=print-plan ;;
  *) usage ;;
esac
if [ "$#" -gt 1 ]; then
  usage
fi

if [ "$mode" != "print-plan" ] || [ -n "${SITE_DOMAIN:-}" ]; then
  [ -n "${SITE_DOMAIN:-}" ] || die "set SITE_DOMAIN to the hostname nginx should serve"
  [[ "$SITE_DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$ ]] ||
    die "SITE_DOMAIN must be a hostname such as datum.studio (use punycode for IDN)"
fi

render_locations() {
  if [ "$SITE_PREFIX" = "/" ]; then
    cat <<'EOF'
    root /var/www/datum/current/public;
    index index.html;

    location = /api/brief {
        proxy_pass http://127.0.0.1:4174/api/brief;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $brief_ip;
        proxy_read_timeout 30s;
    }

    location / {
        try_files $uri $uri/ =404;
    }
EOF
    return
  fi
  cat <<EOF
    location = ${SITE_PREFIX}/api/brief {
        proxy_pass http://127.0.0.1:4174/api/brief;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$brief_ip;
        proxy_read_timeout 30s;
    }

    location = ${SITE_PREFIX} {
        return 301 ${SITE_PREFIX}/;
    }

    location ${SITE_PREFIX}/ {
        alias /var/www/datum/current/public/;
        index index.html;
    }

    location / {
        return 404;
    }
EOF
}

render_nginx() {
  local loc
  command -v awk >/dev/null || die "awk is missing"
  loc=$(mktemp)
  render_locations >"$loc"
  if ! awk -v listen="$listen_port" -v domain="$SITE_DOMAIN" -v locfile="$loc" '
    {
      gsub(/__LISTEN__/, listen)
      gsub(/__SITE_DOMAIN__/, domain)
      if ($0 ~ /__LOCATIONS__/) {
        while ((getline line < locfile) > 0) print line
        close(locfile)
      } else {
        print
      }
    }
  ' "$here/nginx-site.conf"; then
    rm -f "$loc"
    die "could not render the nginx site"
  fi
  rm -f "$loc"
}

if [ "$mode" = "print-plan" ]; then
  printf 'host_mode=%s\n' "$host_mode"
  printf 'listen_port=%s\n' "$listen_port"
  printf 'site_prefix=%s\n' "$SITE_PREFIX"
  if [ "$host_mode" = "dedicated" ]; then
    printf 'configure_ssh=yes\n'
    printf 'configure_firewall=yes\n'
  else
    printf 'configure_ssh=no\n'
    printf 'configure_firewall=no\n'
  fi
  exit 0
fi

if [ "$mode" = "print-nginx" ]; then
  render_nginx
  exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
  die "run this as root"
fi

[ -n "${DEPLOY_AUTHORIZED_KEY_FILE:-}" ] || die "set DEPLOY_AUTHORIZED_KEY_FILE to the deploy user's public key"
[ -f "$DEPLOY_AUTHORIZED_KEY_FILE" ] || die "DEPLOY_AUTHORIZED_KEY_FILE is not a file"

seen=0
while IFS= read -r line || [ -n "$line" ]; do
  line=${line%$'\r'}
  [ -z "$line" ] && continue
  case "$line" in
    \#*) continue ;;
    ssh-ed25519\ * | ssh-rsa\ * | ecdsa-sha2-nistp256\ * | ecdsa-sha2-nistp384\ * | ecdsa-sha2-nistp521\ *)
      seen=1
      ;;
    *)
      die "DEPLOY_AUTHORIZED_KEY_FILE has a line that is not an OpenSSH public key"
      ;;
  esac
done <"$DEPLOY_AUTHORIZED_KEY_FILE"
[ "$seen" -eq 1 ] || die "DEPLOY_AUTHORIZED_KEY_FILE is empty"

echo "setup-server.sh: mode ${host_mode}, prefix ${SITE_PREFIX}, port ${listen_port}"
if [ "$host_mode" = "shared" ]; then
  echo "shared host: not changing sshd or the firewall, and not binding 80 or 443"
fi

policy_added=0
tmp=
backup=
cleanup() {
  if [ "$policy_added" -eq 1 ]; then
    rm -f /usr/sbin/policy-rc.d
  fi
  if [ -n "$tmp" ]; then
    rm -rf "$tmp"
  fi
  if [ -n "$backup" ]; then
    rm -rf "$backup"
  fi
}
trap cleanup EXIT

export DEBIAN_FRONTEND=noninteractive
apt-get update
if [ "$host_mode" = "dedicated" ]; then
  apt-get install -y nginx python3 ufw sudo curl ca-certificates openssh-server iproute2
else
  # Do not let the package start nginx. The default site listens on 80.
  policy_added=1
  cat >/usr/sbin/policy-rc.d <<'EOF'
#!/bin/sh
exit 101
EOF
  chmod 755 /usr/sbin/policy-rc.d
  apt-get install -y nginx python3 sudo curl ca-certificates
  policy_added=0
  rm -f /usr/sbin/policy-rc.d
fi

[ -x /usr/bin/python3 ] || die "python3 is not at /usr/bin/python3"
[ -x /usr/bin/systemctl ] || die "systemctl is not at /usr/bin/systemctl"
[ -x /usr/sbin/nginx ] || die "nginx is not at /usr/sbin/nginx"
command -v flock >/dev/null || die "flock is missing"
command -v curl >/dev/null || die "curl is missing"
grep -F -q 'include /etc/nginx/mime.types;' /etc/nginx/nginx.conf ||
  die "nginx.conf does not include mime.types"
grep -F -q 'include /etc/nginx/conf.d/*.conf;' /etc/nginx/nginx.conf ||
  die "nginx.conf does not include conf.d"
grep -F -q 'include /etc/nginx/sites-enabled/*;' /etc/nginx/nginx.conf ||
  die "nginx.conf does not include sites-enabled"

# dedicated-host-only: sshd on 4041 and the firewall. Shared setup never calls this.
configure_dedicated_host() {
  command -v ss >/dev/null || die "ss is missing"
  [ -x /usr/sbin/sshd ] || die "sshd is not at /usr/sbin/sshd"
  [ -f /etc/ssh/sshd_config ] || die "sshd_config is missing"
  grep -F -q 'Include /etc/ssh/sshd_config.d/*.conf' /etc/ssh/sshd_config ||
    die "sshd_config does not include sshd_config.d"

  ufw allow 4041/tcp
  ufw allow 443/tcp

  # sshd listens on 4041. Reload keeps the session that is running this script.
  port_file=/etc/ssh/sshd_config.d/99-datum-port.conf
  install -d -m 755 -o root -g root /etc/ssh/sshd_config.d
  cat >"$port_file" <<'EOF'
Port 4041
EOF
  chmod 644 "$port_file"
  chown root:root "$port_file"
  sshd -t || die "sshd rejected the config"
  systemctl enable ssh
  if systemctl is-active --quiet ssh; then
    systemctl reload ssh
  else
    systemctl start ssh
  fi
  ready=0
  i=0
  while [ "$i" -lt 20 ]; do
    if ss -H -ltn 'sport = :4041' | grep -q .; then
      ready=1
      break
    fi
    i=$((i + 1))
    sleep 0.25
  done
  [ "$ready" -eq 1 ] || die "sshd is not listening on port 4041"
  effective=$(sshd -T | awk '$1 == "port" { print $2 }')
  [ "$effective" = "4041" ] || die "sshd is not configured for 4041 only"

  ufw default deny incoming
  ufw default allow outgoing
  ufw --force enable
}

install -d -m 755 -o root -g root /etc/datum

deploy_created=0
if ! id -u deploy >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash --home-dir /home/deploy deploy
  deploy_created=1
fi
if [ "$host_mode" = "shared" ] && [ "$deploy_created" -eq 0 ] && [ ! -f /etc/datum/deploy-user ]; then
  die "user deploy already exists; this setup will not change another account"
fi
printf '%s\n' deploy >/etc/datum/deploy-user
chmod 644 /etc/datum/deploy-user

datum_created=0
if ! id -u datum >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin datum
  datum_created=1
fi
if [ "$host_mode" = "shared" ] && [ "$datum_created" -eq 0 ] && [ ! -f /etc/datum/service-user ]; then
  die "user datum already exists; this setup will not change another account"
fi
printf '%s\n' datum >/etc/datum/service-user
chmod 644 /etc/datum/service-user

passwd -l deploy >/dev/null
chmod 750 /home/deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
install -m 600 -o deploy -g deploy "$DEPLOY_AUTHORIZED_KEY_FILE" /home/deploy/.ssh/authorized_keys

if [ -e /var/www/datum ]; then
  owner=$(stat -c '%U' /var/www/datum)
  if [ "$owner" != "deploy" ]; then
    die "/var/www/datum is owned by ${owner}; this setup will not take it"
  fi
fi
install -d -m 755 -o deploy -g deploy /var/www/datum
install -d -m 755 -o deploy -g deploy /var/www/datum/releases
install -d -m 755 -o root -g root /etc/ssl/cloudflare

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
cat >"$sudoers" <<'EOF'
deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart datum-brief.service
EOF
visudo -cf "$sudoers" >/dev/null
install -m 440 -o root -g root "$sudoers" /etc/sudoers.d/datum-deploy
rm -f "$sudoers"

if [ "$host_mode" = "dedicated" ]; then
  configure_dedicated_host
fi

is_cidr() {
  case "$1" in
    0.0.0.0/0 | ::/0) return 1 ;;
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
  done <"$dest"
  [ "$count" -ge 4 ] || die "$url returned too few addresses"
}

tmp=$(mktemp -d)
fetch_cidrs https://www.cloudflare.com/ips-v4 "$tmp/v4"
fetch_cidrs https://www.cloudflare.com/ips-v6 "$tmp/v6"

# nginx $variables. Shell must not expand them.
# shellcheck disable=SC2016
{
  echo 'geo $remote_addr $is_cloudflare {'
  echo '    default 0;'
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%$'\r'}
    [ -z "$line" ] && continue
    printf '    %s 1;\n' "$line"
  done <"$tmp/v4"
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%$'\r'}
    [ -z "$line" ] && continue
    printf '    %s 1;\n' "$line"
  done <"$tmp/v6"
  echo '}'
  echo
  echo 'map $http_cf_connecting_ip $brief_ip {'
  echo '    ""      $remote_addr;'
  echo '    default $http_cf_connecting_ip;'
  echo '}'
} >"$tmp/datum-cloudflare.conf"

render_nginx >"$tmp/datum"

backup=$(mktemp -d)
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

had_default=0
if [ -e /etc/nginx/sites-enabled/default ] || [ -L /etc/nginx/sites-enabled/default ]; then
  had_default=1
fi
rm -f /etc/nginx/sites-enabled/default
if ! nginx -t; then
  if [ "$had_default" -eq 1 ]; then
    ln -sfn /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default
  fi
  restore_nginx
  die "nginx rejected the config after disabling the default site"
fi

# Shared nginx must not listen on the other service's ports.
refuse_web_ports() {
  local dump
  dump=$(nginx -T 2>/dev/null || true)
  [ -n "$dump" ] || return 1
  if printf '%s\n' "$dump" | grep -E -q 'listen[[:space:]]+(\[::\]:)?(80|443)([^0-9]|$)'; then
    return 1
  fi
  printf '%s\n' "$dump" | grep -E -q "listen[[:space:]]+(\[::\]:)?${listen_port}([^0-9]|$)"
}

if [ "$host_mode" = "shared" ] && ! refuse_web_ports; then
  if [ "$had_default" -eq 1 ]; then
    ln -sfn /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default
  fi
  restore_nginx
  die "refusing to bind port 80 or 443"
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

if [ "$host_mode" = "dedicated" ]; then
  echo "setup complete for $SITE_DOMAIN prefix / on port 443"
else
  echo "setup complete for $SITE_DOMAIN prefix $SITE_PREFIX on port $listen_port"
  echo "nginx listens on $listen_port only. sshd and the firewall were not changed."
  echo "Add a Cloudflare origin rule that sends only this prefix to port $listen_port. README.md has the expression."
  echo "Other paths stay on port 443. This script does not edit the other service."
fi
if ! grep -q '^SMTP_HOST=.\+' /etc/datum/form.env; then
  echo "edit /etc/datum/form.env, then run ./deploy/deploy.sh from your workstation"
fi
echo "DNS is not managed here. Proxy the hostname at Cloudflare, set SSL to Full (strict), and turn on Always Use HTTPS."
echo "re-run this script when you want a fresh Cloudflare address list"
