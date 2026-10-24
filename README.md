# Datum

Local site for Datum, an embedded product studio. The work runs from concept design to hardware ready for mass production: mechanical, electrical, and firmware.

## Service

The page is a user service, `datum-site.service`, on http://127.0.0.1:4173/

```bash
systemctl --user status datum-site.service
systemctl --user restart datum-site.service
```

Logs: `journalctl --user -u datum-site.service -e`

The unit file is `~/.config/systemd/user/datum-site.service`.

The studio name, the case studies, and `hello@datum.studio` live in `index.html` and `js/main.js`.

## Hosting

nginx serves the page. A Python process on the same machine emails the project brief over SMTP. There is no database. The local preview above does not accept the form; `POST /api/brief` exists only behind nginx.

The server is assumed to be Debian 12 or Ubuntu 22.04+, with SSH on port 22. You supply the host, the domain, the origin certificate, and the SMTP account. Nothing here creates DNS records or calls the Cloudflare API.

### Config

Copy `deploy/deploy.env.example` to `deploy/deploy.env`:

- `DEPLOY_HOST` — server hostname or IPv4 address
- `DEPLOY_USER` — SSH user, default `deploy`
- `DEPLOY_SSH_KEY` — path to that user's private key

On the server, `/etc/datum/form.env` (created from `deploy/form.env.example` on first setup):

- `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` — authenticated submission
- `SMTP_FROM` — the address the provider lets this server send as
- `MAIL_TO` — inbox that receives briefs, `hello@datum.studio` in the example

Mail is STARTTLS on port 587 only. The page still shows `hello@datum.studio`; set `MAIL_TO` to that address unless the form should go somewhere else.

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

The script is safe to run again. It installs nginx, Python 3, and ufw; creates the `deploy` and `datum` users; installs the systemd unit; allows inbound 22, 80, and 443; and refuses HTTP unless the peer is a Cloudflare address. Re-running refreshes that address list. It removes nginx's default site. It does not overwrite `/etc/datum/form.env` after the first run.

Edit `/etc/datum/form.env`, then deploy. Deploy restarts the form service, which rereads that file.

### Deploy

From a checkout, with `deploy/deploy.env` filled in:

```bash
./deploy/deploy.sh
```

That copies the site and `server/brief.py` into `/var/www/datum/releases/<timestamp>`, points `current` at it, and restarts `datum-brief.service`. nginx reads files through the `current` symlink, so the file switch does not reload nginx. If the new form process does not answer, the script points `current` back at the release it replaced.

```bash
./deploy/deploy.sh rollback
```

Rollback swaps `current` and `previous`, then restarts the form service. The five newest releases are kept, and the current and previous releases are not deleted.

`./deploy/deploy.sh --dry-run` builds a release locally and does not upload it.

### Defaults

- Web server: nginx, TLS with the Cloudflare origin certificate
- Form: Python 3 standard library on `127.0.0.1:4174`, unit `datum-brief.service`, user `datum`
- Spam: a hidden fax field. A filled field is discarded and reported as sent
- Firewall: ufw, inbound 22, 80, and 443. nginx returns 403 for peers that are not Cloudflare
- Releases: `/var/www/datum`, switched with a symlink
- DNS: not managed by these scripts

Check the form handler and the shell scripts with:

```bash
python3 -m unittest discover -s server -v
bash -n deploy/deploy.sh deploy/setup-server.sh deploy/switch.sh
./deploy/deploy.sh --dry-run
```
