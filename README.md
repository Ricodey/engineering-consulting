# Datum

Static page for Datum, an embedded product studio, and a Python process that emails the project brief. The work runs from concept design to hardware ready for mass production: mechanical, electrical, and firmware.

There is no database, queue, or application framework. Nothing in this repository creates DNS records or calls the Cloudflare API.

The studio name, the case studies, and `hello@datum.studio` live in `index.html` and `js/main.js`.

## Layout

- `index.html`, `css/`, `js/`, `assets/` — the page. `deploy/deploy.sh` copies these paths from the repository root.
- `server/brief.py` — `POST /api/brief` on the handler. Python 3 standard library only. Listens on `127.0.0.1:4174`. nginx exposes that as `{prefix}/api/brief`.
- `server/test_brief.py`, `server/test_prefix.py` — handler tests, page markup, and the prefix config.
- `deploy/` — nginx site, the `datum-brief` systemd unit, server setup, and deploy.

## Run

### Page

From the repository root:

```bash
python3 -m http.server 4173 --bind 127.0.0.1
```

Open http://127.0.0.1:4173/. This preview does not accept the form. `POST /api/brief` exists only behind nginx.

Where the page is already installed as a user service, that same address is `datum-site.service`. The unit file is `~/.config/systemd/user/datum-site.service` and is not in this repository.

```bash
systemctl --user status datum-site.service
systemctl --user restart datum-site.service
```

Logs: `journalctl --user -u datum-site.service -e`

### Form handler

The handler reads SMTP settings from the environment. Copy `deploy/form.env.example` to `deploy/form.env` (gitignored) and fill it in. Do not commit passwords. If a value contains spaces or `#`, wrap it in double quotes.

```bash
set -a
. deploy/form.env
set +a
python3 server/brief.py
```

It listens on `127.0.0.1:4174`. `GET /health` returns `{"ok": true}`. Mail is STARTTLS on port 587. Without `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, and `MAIL_TO`, the process still listens and `POST /api/brief` answers 503.

## Test

There is no compile step. From the repository root:

```bash
python3 -m unittest discover -s server -v
bash -n deploy/deploy.sh deploy/setup-server.sh deploy/switch.sh
./deploy/deploy.sh --dry-run
```

The prefix tests run `sudo nginx -t` on the rendered site, because that check binds the listen port. Install nginx first (`sudo apt-get install -y nginx`) if it is not already there.

`./deploy/deploy.sh --dry-run` stages a release in a temporary directory and prints the file list. It does not read `deploy/deploy.env` and does not upload.

Lint, with Ruff 0.17.2, ShellCheck, and shfmt (Ubuntu 24.04 packages: ShellCheck 0.9, shfmt 3.8):

```bash
python3 -m pip install ruff==0.17.2
ruff check .
ruff format --check .
shellcheck deploy/deploy.sh deploy/setup-server.sh deploy/switch.sh
shfmt -d deploy/deploy.sh deploy/setup-server.sh deploy/switch.sh
node --check js/main.js
```

Pull requests run these commands in `.github/workflows/ci.yml`. HTML and CSS are not linted.

## Hosting

nginx serves the page. A Python process on the same machine emails the project brief over SMTP. The server is Debian 12 or Ubuntu 22.04+. You supply the host, the domain, the origin certificate, and the SMTP account. nginx serves HTTPS only. Cloudflare handles the HTTP-to-HTTPS redirect (Always Use HTTPS, SSL mode Full strict).

`SITE_PREFIX` is the only switch between the two layouts.

- `SITE_PREFIX=/` (the default) is a dedicated host. nginx listens on 443. Setup sets sshd to port 4041 and the firewall to allow 4041 and 443.
- Any other prefix is a shared host. The test values are `SITE_DOMAIN=pancakeboard.com` and `SITE_PREFIX=/consulting`. nginx listens on 8443 with the origin certificate. Setup does not bind 80 or 443, does not change sshd, and does not change the firewall. The other service keeps 443. Cloudflare sends only that path to port 8443.

Those names are configuration. The scripts do not special-case the test domain.

### Config

Copy `deploy/deploy.env.example` to `deploy/deploy.env` (gitignored). That file is shell assignments.

- `DEPLOY_HOST` — server hostname or IPv4 address
- `DEPLOY_USER` — SSH user, default `deploy`
- `DEPLOY_SSH_PORT` — `4041` on a dedicated host. On a shared host, the port sshd already uses. Setup does not change that port.
- `DEPLOY_SSH_KEY` — path to that user's private key

On the server, `/etc/datum/form.env` (created from `deploy/form.env.example` on first setup):

- `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` — authenticated submission
- `SMTP_FROM` — the address the provider lets this server send as
- `MAIL_TO` — inbox that receives briefs. The example is `hello@datum.studio`

Mail is STARTTLS on port 587 only. The page still shows `hello@datum.studio`. Set `MAIL_TO` to that address unless the form should go somewhere else.

Place the Cloudflare origin certificate before setup:

- `/etc/ssl/cloudflare/origin.pem`
- `/etc/ssl/cloudflare/origin.key`

Create it under SSL/TLS → Origin Server. Set the zone's SSL mode to Full (strict). On a dedicated host, add a proxied DNS record for the hostname yourself. On a shared host, leave the existing record and add the origin rule in the shared-host section.

Setup also needs `SITE_DOMAIN` (the hostname nginx serves), `SITE_PREFIX` (`/` or one segment, such as `/consulting`), and `DEPLOY_AUTHORIZED_KEY_FILE` (the deploy user's public key).

### Dedicated host

Copy the `deploy/` directory to the server and run it as root. `SITE_PREFIX=/` is the default, so the variable may be omitted.

```bash
SITE_DOMAIN=datum.studio \
SITE_PREFIX=/ \
DEPLOY_AUTHORIZED_KEY_FILE=/root/deploy.pub \
  bash deploy/setup-server.sh
```

The script is safe to run again. It installs nginx, Python 3, and ufw; creates the `deploy` and `datum` users; installs the systemd unit; and configures sshd on port 4041. The firewall allows 4041 and 443. nginx listens on 443 only and returns 403 unless the peer is a Cloudflare address. Re-running refreshes that address list. It removes nginx's default site. It does not overwrite `/etc/datum/form.env` after the first run. An existing SSH session stays connected while sshd reloads.

The public brief URL is `POST /api/brief`. nginx forwards it to `127.0.0.1:4174/api/brief`.

### Shared host

Run the same script. Do not run the dedicated command on this machine: `SITE_PREFIX=/` would bind 443 and reconfigure sshd and the firewall.

```bash
SITE_DOMAIN=pancakeboard.com \
SITE_PREFIX=/consulting \
DEPLOY_AUTHORIZED_KEY_FILE=/root/deploy.pub \
  bash deploy/setup-server.sh
```

The script creates users `deploy` and `datum` when those names are free. If either account already exists, it stops and does not change that account. It installs this app's nginx and `datum-brief.service`, and it writes `/etc/nginx/sites-available/datum`. The brief process listens on `127.0.0.1:4174` only.

It does not edit the other service, bind 80 or 443, reconfigure sshd, or run ufw. Re-running refreshes the Cloudflare address list and replaces this app's nginx site. It does not overwrite `/etc/datum/form.env` after the first run.

Place the origin certificate at the same paths as a dedicated host before setup. The certificate must cover `SITE_DOMAIN`, which is the SNI Cloudflare sends. Setup sets those two files to `root` and mode `644` / `600`. If the other service already uses those exact paths, that mode change applies to them too. This repository does not know where the other service stores its certificate.

nginx redirects `/consulting` to `/consulting/` and serves the page under that prefix. Asset URLs in the page are relative to the document, and the form posts to `api/brief`, which resolves to `/consulting/api/brief`. The same files at prefix `/` post to `/api/brief`. nginx forwards `{prefix}/api/brief` to `127.0.0.1:4174/api/brief`. Other paths on port 8443 return 404.

#### Cloudflare origin rule

In the zone: Rules → Origin Rules → Create rule. Destination port override is on every plan. Do not set a DNS record override, and do not change the hostname's proxied DNS record.

Expression, for the test values:

```
(http.host eq "pancakeboard.com" and (http.request.uri.path eq "/consulting" or starts_with(http.request.uri.path, "/consulting/")))
```

Then: Destination Port → Rewrite to → `8443`.

Paths outside `/consulting` stay on port 443. SSL mode stays Full (strict). Always Use HTTPS stays on. If `SITE_PREFIX` or `SITE_DOMAIN` changes, change this expression to match and run setup again. Deploy does not change the prefix.

8443 is a Cloudflare proxied HTTPS port. Caching is off on that port unless an Enterprise cache rule turns it on. The page already sends `Cache-Control: no-cache`. The managed WAF rule "Anomaly:Port - Non Standard Port (not 80 or 443)" is off by default. If it is enabled, it can block this origin port. Leave it off for this path.

#### Firewall and SSH

Setup does not know the other service's firewall or SSH port, so it changes neither. If a firewall drops inbound traffic, Cloudflare cannot reach the origin until TCP 8443 is allowed. Do not remove the other service's port 443.

In `deploy/deploy.env`, `DEPLOY_SSH_PORT` is the port sshd already uses. `DEPLOY_HOST` is an address that sshd accepts. From a machine that is already on the server, `127.0.0.1` is the usual host.

Edit `/etc/datum/form.env`, then deploy. Deploy restarts the form service, which rereads that file.

### Deploy

From a checkout, with `deploy/deploy.env` filled in:

```bash
./deploy/deploy.sh
```

That copies the site and `server/brief.py` into `/var/www/datum/releases/<timestamp>` (`public/` for the page, `app/brief.py` for the handler), points `current` at it, and restarts `datum-brief.service`. nginx reads files through the `current` symlink, so the file switch does not reload nginx. If the new form process does not answer `GET /health`, the script points `current` back at the release it replaced. On the first deploy, a failed health check removes `current`.

```bash
./deploy/deploy.sh rollback
```

Rollback swaps `current` and `previous`, then restarts the form service. If the restored process does not answer, the links stay on the restored release and the script exits 1. The five newest releases are kept, and the current and previous releases are not deleted.

### Defaults

- Dedicated web server: nginx on 443, TLS with the Cloudflare origin certificate. Cloudflare redirects HTTP to HTTPS
- Shared web server: nginx on 8443 for `SITE_PREFIX`, same certificate. Port 443 stays with the other service. Cloudflare origin rule above
- Form: Python 3 standard library on `127.0.0.1:4174`, unit `datum-brief.service`, user `datum`. Public path `{prefix}/api/brief`
- Spam: a hidden fax field. A filled field is discarded and reported as sent
- Dedicated firewall: ufw, inbound 4041 and 443. nginx returns 403 for peers that are not Cloudflare
- Shared firewall: unchanged. nginx on 8443 still returns 403 for peers that are not Cloudflare
- Dedicated SSH: port 4041. Shared SSH: unchanged. `deploy.sh` uses `DEPLOY_SSH_PORT`
- Releases: `/var/www/datum`, switched with a symlink. The prefix lives in the nginx site, not in the release
- DNS: not managed by these scripts
