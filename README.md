# Datum

Static page for Datum, an embedded product studio, and a Python process that emails the project brief. The work runs from concept design to hardware ready for mass production: mechanical, electrical, and firmware.

There is no database, queue, or application framework. Nothing in this repository creates DNS records or calls the Cloudflare API.

The studio name, the case studies, and `hello@datum.studio` live in `index.html` and `js/main.js`.

## Layout

- `index.html`, `css/`, `js/`, `assets/` — the page. `deploy/deploy.sh` copies these paths from the repository root.
- `server/brief.py` — `POST /api/brief` on the handler. Python 3 standard library only. Listens on `127.0.0.1:4174` inside the pod. nginx proxies that path.
- `server/test_brief.py`, `server/test_prefix.py` — handler tests, page markup, and the pod config.
- `deploy/` — the datum pod, the copy and install scripts, and deploy.

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
bash -n deploy/deploy.sh deploy/datum.sh deploy/copy.sh
./deploy/deploy.sh --dry-run
```

The pod tests run `nginx -t` on `deploy/pod/nginx.conf`. Install nginx first (`sudo apt-get install -y nginx`) if it is not already there. The check listens on port 8080 inside the test config and does not need root.

`./deploy/deploy.sh --dry-run` stages a release in a temporary directory and prints the file list. It does not read `deploy/deploy.env` and does not upload.

Lint, with Ruff 0.17.2, ShellCheck, and shfmt (Ubuntu 24.04 packages: ShellCheck 0.9, shfmt 3.8):

```bash
python3 -m pip install ruff==0.17.2
ruff check .
ruff format --check .
shellcheck deploy/deploy.sh deploy/datum.sh deploy/copy.sh
shfmt -d deploy/deploy.sh deploy/datum.sh deploy/copy.sh
node --check js/main.js
```

Pull requests run these commands in `.github/workflows/ci.yml`. HTML and CSS are not linted.

## Hosting

The site runs as a rootless Podman pod owned by user `datum`. nginx and the brief handler are containers in that pod. The pod publishes `127.0.0.1:8443` on the host and no other port. The brief handler listens on `127.0.0.1:4174` inside the pod.

The test name is `https://datum.pancakeboard.com/`. Add that public hostname on the tunnel that is already running, with service `http://127.0.0.1:8443`. Leave the `pancakeboard.com` hostname as it is. A later domain uses its own tunnel token against the same loopback port.

Bootstrap does not change sshd or the firewall, and it does not write another account's files.

### Config

Copy `deploy/deploy.env.example` to `deploy/deploy.env` (gitignored). That file is shell assignments.

- `DEPLOY_HOST` — server hostname or IPv4 address
- `DEPLOY_USER` — `datum`
- `DEPLOY_SSH_PORT` — the port sshd already uses
- `DEPLOY_SSH_KEY` — path to the datum private key
- `DEPLOY_SSH_KNOWN_HOSTS` — path to a `known_hosts` line for that host and port

Copy `deploy/form.env.example` to `deploy/form.env` (gitignored) and fill in SMTP. Copy `deploy/copy.defaults.example` to `deploy/copy.defaults` (gitignored) and point `FORM_ENV` at that file. `SSH_USER` stays `datum`.

On the server, install stores SMTP at `/home/datum/form.env`. Mail is STARTTLS on port 587. The page still shows `hello@datum.studio`.

### First run

Two commands, then a release. `copy.sh` logs in as `datum`, so the account has to exist first. Root SSH stays closed, so the first command runs in the provider console, as root. `DEPLOY_AUTHORIZED_KEY` is the one datum public key line.

```bash
curl -fsSL -o /tmp/datum.sh https://raw.githubusercontent.com/Ricodey/engineering-consulting/main/deploy/datum.sh
DEPLOY_AUTHORIZED_KEY='ssh-ed25519 AAAA... datum-deploy' bash /tmp/datum.sh bootstrap
rm -f /tmp/datum.sh
```

That creates user `datum`, a subuid range that does not overlap an existing account, and the authorized key. It enables lingering so the user services keep running after logout. If the `podman` command is missing, bootstrap installs the distro packages `podman` and `uidmap`. An existing Podman is left in place. Quadlets need Podman 4.4 or newer. Bootstrap does not change sshd or the firewall.

From a checkout with `deploy/copy.defaults` filled in:

```bash
./deploy/copy.sh
```

`copy.sh` uses `scp` as `datum` and writes these paths, then runs `bash /home/datum/incoming/datum.sh install` on that login:

- `/home/datum/incoming/datum.sh`
- `/home/datum/incoming/form.env` (the file `FORM_ENV` points at)
- `/home/datum/incoming/pod/datum.pod`
- `/home/datum/incoming/pod/datum-web.container`
- `/home/datum/incoming/pod/datum-brief.container`
- `/home/datum/incoming/pod/nginx.conf`

`install` moves those files to `/home/datum/form.env` (mode 600), `/home/datum/nginx.conf`, and `/home/datum/.config/containers/systemd/`. When `current` is missing it creates an empty `/home/datum/releases/initial` and points `current` at it. It deletes `/home/datum/incoming/` and restarts the pod. The first start pulls `docker.io/library/nginx:1.28-alpine`. The brief image, `docker.io/library/python:3.12-slim`, is pulled once `app/brief.py` is in the release. Run `./deploy/copy.sh` again when SMTP, nginx, or the quadlets change.

`./deploy/deploy.sh` sends the page. It builds `public/` (`index.html`, `css/`, `js/`, `assets/` from the repository root) and `app/brief.py` (from `server/brief.py`), then `scp`s those two directories to `/home/datum/releases/<timestamp>/`. The server needs OpenSSH. The script points `current` at that release and restarts `datum-web` and `datum-brief`.

```bash
./deploy/deploy.sh
```

A published GitHub release runs only that page upload. SMTP and the pod files stay as `install` left them.

### Remove

On the server, as root, when the site leaves the machine:

```bash
bash deploy/datum.sh remove
```

That stops the datum units and deletes user `datum` and `/home/datum`. It refuses any path under `/home/pancake` or `/etc/pancake`.

### Deploy

From a checkout, with `deploy/deploy.env` filled in:

```bash
./deploy/deploy.sh
```

That `scp`s `public/` and `app/brief.py` into `/home/datum/releases/<timestamp>`, points `current` at it, and restarts `datum-web` and `datum-brief`. If the new brief process does not answer `GET /health`, the script points `current` back at the previous release.

```bash
./deploy/deploy.sh rollback
```

Rollback swaps `current` and `previous`, then restarts those two units. The five newest releases are kept, and the current and previous releases are not deleted.

## Release

Publishing a GitHub release deploys that tag. The workflow is `.github/workflows/release.yml`. It checks out the release tag and runs `./deploy/deploy.sh` on a GitHub-hosted runner, and only for `Ricodey/engineering-consulting`. The job SSHes in as `datum`.

### Secrets

Repository secrets, under Settings → Secrets and variables → Actions. None of these belong in the repo.

- `DEPLOY_HOST` — the server address
- `DEPLOY_USER` — `datum`
- `DEPLOY_SSH_PORT` — the SSH port already in use
- `DEPLOY_SSH_KEY` — the private key that matches the bootstrap public key, including the BEGIN and END lines
- `DEPLOY_SSH_KNOWN_HOSTS` — the server host key line for `DEPLOY_HOST` and `DEPLOY_SSH_PORT`

SMTP stays in `/home/datum/form.env` on the server. The workflow does not send mail and does not take SMTP secrets.

The job writes those values into `deploy/deploy.env` for `deploy.sh`, then deletes that file and the key. If the new brief process does not answer `GET /health`, `deploy.sh` points `current` back at the previous release and the job fails. Moving the pod to another server is a new `DEPLOY_HOST` and the same install.
