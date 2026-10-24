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

Copy `deploy/form.env.example` to `deploy/form.env` (gitignored) and fill in SMTP. Copy `deploy/copy.defaults.example` to `deploy/copy.defaults` (gitignored) and point `FORM_ENV` at that file. `SSH_USER` stays `datum`.

On the server, install stores SMTP at `/home/datum/form.env`. Mail is STARTTLS on port 587. The page still shows `hello@datum.studio`.

### First run

`datum` cannot receive a file until the account exists. On the server, as root, with the datum public key:

```bash
DEPLOY_AUTHORIZED_KEY_FILE=/path/to/datum.pub bash deploy/datum.sh bootstrap
```

That creates user `datum`, a subuid range that does not overlap an existing account, and the authorized key. Then, from a checkout:

```bash
./deploy/copy.sh
ssh -p 4041 -i ~/.ssh/datum-deploy datum@HOST bash /home/datum/incoming/datum.sh install
```

`copy.sh` logs in as `datum` and uploads the pod templates and `form.env` to `/home/datum/incoming/`. `install` stores the env file, writes the quadlets, and starts the pod. Then deploy the page:

```bash
./deploy/deploy.sh
```

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

That copies the site and `server/brief.py` into `/home/datum/releases/<timestamp>`, points `current` at it, and restarts `datum-web` and `datum-brief`. If the new brief process does not answer `GET /health`, the script points `current` back at the previous release.

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

SMTP stays in `/home/datum/form.env` on the server. The workflow does not send mail and does not take SMTP secrets.

The job writes those values into `deploy/deploy.env` for `deploy.sh`, then deletes that file and the key. If the new brief process does not answer `GET /health`, `deploy.sh` points `current` back at the previous release and the job fails. Moving the pod to another server is a new `DEPLOY_HOST` and the same install.
