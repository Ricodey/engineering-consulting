# Datum

Static page for Datum, an embedded product studio, and a Python process that emails the project brief. The work runs from concept design to hardware ready for mass production: mechanical, electrical, and firmware.

There is no database, queue, or application framework. Nothing in this repository creates DNS records or calls the Cloudflare API.

The studio name, the case studies, and `hello@datum.studio` live in `index.html` and `js/main.js`.

## Layout

- `index.html`, `css/`, `js/`, `assets/` — the page. `deploy/deploy.sh` copies these paths from the repository root.
- `server/brief.py` — `POST /api/brief`. Python 3 standard library only. Listens on `127.0.0.1:4174`.
- `server/test_brief.py` — tests for the handler and for the form markup in the page.
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

nginx serves the page. A Python process on the same machine emails the project brief over SMTP. The server is assumed to be Debian 12 or Ubuntu 22.04+, with SSH on port 4041. You supply the host, the domain, the origin certificate, and the SMTP account. nginx serves HTTPS only. Cloudflare handles the HTTP-to-HTTPS redirect (Always Use HTTPS, SSL mode Full strict).

### Config

Copy `deploy/deploy.env.example` to `deploy/deploy.env` (gitignored). That file is shell assignments.

- `DEPLOY_HOST` — server hostname or IPv4 address
- `DEPLOY_USER` — SSH user, default `deploy`
- `DEPLOY_SSH_PORT` — `4041`
- `DEPLOY_SSH_KEY` — path to that user's private key

On the server, `/etc/datum/form.env` (created from `deploy/form.env.example` on first setup):

- `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` — authenticated submission
- `SMTP_FROM` — the address the provider lets this server send as
- `MAIL_TO` — inbox that receives briefs. The example is `hello@datum.studio`

Mail is STARTTLS on port 587 only. The page still shows `hello@datum.studio`. Set `MAIL_TO` to that address unless the form should go somewhere else.

Place the Cloudflare origin certificate before setup:

- `/etc/ssl/cloudflare/origin.pem`
- `/etc/ssl/cloudflare/origin.key`

Create it under SSL/TLS → Origin Server. Set the zone's SSL mode to Full (strict). Add a proxied DNS record for the hostname yourself.

Setup also needs `SITE_DOMAIN` (the hostname nginx serves) and `DEPLOY_AUTHORIZED_KEY_FILE` (the deploy user's public key).

### Server setup

Copy the `deploy/` directory to the server and run it as root:

```bash
SITE_DOMAIN=datum.studio \
DEPLOY_AUTHORIZED_KEY_FILE=/root/deploy.pub \
  bash deploy/setup-server.sh
```

The script is safe to run again. It installs nginx, Python 3, and ufw; creates the `deploy` and `datum` users; installs the systemd unit; and configures sshd on port 4041. The firewall allows 4041 and 443. nginx listens on 443 only and returns 403 unless the peer is a Cloudflare address. Re-running refreshes that address list. It removes nginx's default site. It does not overwrite `/etc/datum/form.env` after the first run. An existing SSH session stays connected while sshd reloads.

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

- Web server: nginx on 443, TLS with the Cloudflare origin certificate. Cloudflare redirects HTTP to HTTPS
- Form: Python 3 standard library on `127.0.0.1:4174`, unit `datum-brief.service`, user `datum`
- Spam: a hidden fax field. A filled field is discarded and reported as sent
- Firewall: ufw, inbound 4041 and 443. nginx returns 403 for peers that are not Cloudflare
- SSH: port 4041. `deploy.sh` uses that port
- Releases: `/var/www/datum`, switched with a symlink
- DNS: not managed by these scripts
