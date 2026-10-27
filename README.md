# Datum

Static page for Datum, an embedded product studio, and a Python process that emails the project brief. The work runs from concept design to hardware ready for mass production: mechanical, electrical, and firmware.

There is no database, queue, or application framework. The page does not call Cloudflare. A release runs `deploy/cloudflare_tunnel.py` (Python 3 standard library) to create the datum tunnel and point `datum.pancakeboard.com` at it.

The studio name, the case studies, and `hello@datum.studio` live in `index.html` and `js/main.js`.

## Layout

- `index.html`, `css/`, `js/`, `assets/` — the page. `deploy/deploy.sh` copies these paths from the repository root.
- `server/brief.py` — `POST /api/brief` on the handler. Python 3 standard library only. Listens on `127.0.0.1:4174` inside the pod. nginx proxies that path.
- `server/preview.py` — the local site on `127.0.0.1:4173`. It serves the page and the same handler. A release does not copy it.
- `server/test_brief.py`, `server/test_prefix.py`, `server/test_preview.py`, `server/test_runner.py`, `server/test_tunnel.py`, `server/test_secret.py` — handler tests, page markup, the pod config, the release workflow, the tunnel cutover, and the Podman secret.
- `deploy/` — the datum pod, the copy and install scripts, `cloudflare_tunnel.py`, and deploy.

## Run

Run the site on the computer where you edit it. Open http://127.0.0.1:4173/ and reload after a page change. When the change is ready, publish a release. That deploys the tag with `./deploy/deploy.sh`.

From the repository root:

```bash
python3 server/preview.py
```

The process serves the page and `POST /api/brief` on `127.0.0.1:4173`. It reads `index.html`, `css/`, `js/`, and `assets/` from the checkout on each request. It does not read a mail env file. Without `SMTP_PASSWORD` in the environment, a valid brief returns 503 and the page says the request did not send.

If `datum-site.service` is installed, it runs that command. The unit file is `~/.config/systemd/user/datum-site.service` and is not in this repository. Restart it after a change to `server/brief.py` or `server/preview.py`:

```bash
systemctl --user restart datum-site.service
```

Logs: `journalctl --user -u datum-site.service -e`

The pod runs `server/brief.py` on `127.0.0.1:4174` inside the pod. nginx proxies `/api/brief` there. Mail is STARTTLS on port 587 to `smtp.gmail.com`, authenticated as `fig@mail.grokbot.com`, delivered to `fig@mail.grokbot.com`, with Reply-To set to the submitter. The password is `SMTP_PASSWORD` from the environment. To run that process by itself, export `SMTP_PASSWORD` and run `python3 server/brief.py`. Do not commit the password. The process does not read a form env file.

`GET /health` returns `{"ok": true}`. Without `SMTP_PASSWORD`, the process still listens and `POST /api/brief` answers 503.

## Test

There is no compile step. From the repository root:

```bash
python3 -m unittest discover -s server -v
bash -n deploy/deploy.sh deploy/datum.sh deploy/copy.sh deploy/stage.sh
./deploy/deploy.sh --dry-run
```

The pod tests run `nginx -t` on `deploy/pod/nginx.conf`. Install nginx first (`sudo apt-get install -y nginx`) if it is not already there. The check listens on port 8080 inside the test config and does not need root.

`./deploy/deploy.sh --dry-run` stages a release in a temporary directory and prints the file list. It does not read `deploy/deploy.env` and does not upload.

Lint, with Ruff 0.17.2, ShellCheck, and shfmt (Ubuntu 24.04 packages: ShellCheck 0.9, shfmt 3.8):

```bash
python3 -m pip install ruff==0.17.2
ruff check .
ruff format --check .
shellcheck deploy/deploy.sh deploy/datum.sh deploy/copy.sh deploy/stage.sh
shfmt -d deploy/deploy.sh deploy/datum.sh deploy/copy.sh deploy/stage.sh
node --check js/main.js
```

Pull requests run these commands in `.github/workflows/ci.yml`. HTML and CSS are not linted.

## Hosting

The site runs as a rootless Podman pod owned by user `datum`. nginx, the brief handler, and cloudflared are containers in that pod. The pod publishes `127.0.0.1:8443` on the host and no other port. Inside the pod, nginx listens on port 8080 and the brief handler listens on `127.0.0.1:4174`. cloudflared dials `http://127.0.0.1:8080` on the pod network. Nothing listens on port 80 or 443.

The public name is `https://datum.pancakeboard.com/`. `datum-cloudflared` runs `docker.io/cloudflare/cloudflared:2026.10.4` with `tunnel --no-autoupdate run` in the site pod. The connector token is a Podman secret named `datum-cloudflared`, and the container reads it as `TUNNEL_TOKEN`. The unit file does not contain the token, and the token is not committed. A release runs `podman secret create --replace`. Deploy deletes a leftover `/home/datum/tunnel.env`.

The mailbox password is a Podman secret named `datum-smtp`. The brief container reads it as `SMTP_PASSWORD`. The unit file does not contain the password. A release runs `podman secret create --replace` for `datum-smtp` the same way it stores the tunnel token. Deploy deletes a leftover `/home/datum/form.env`. SSH stays on port 4041. Nothing listens on port 80 or 443.

`./deploy/deploy.sh` and the release workflow do the cutover, and they are safe to run again. They ensure a remotely managed tunnel named `datum`, set its ingress to `datum.pancakeboard.com` at `http://127.0.0.1:8080` plus a 404 catch-all, and store the connector token as that Podman secret. They replace the pod and wait until the tunnel is healthy. Only then do they point the proxied CNAME `datum.pancakeboard.com` at `<tunnel-id>.cfargotunnel.com`. Then they remove only the `datum.pancakeboard.com` ingress rule from any other tunnel that has it. That tunnel is found by the hostname rule. Its other rules stay, and the tunnel is not deleted. A re-run does not create a second `datum` tunnel, does not rewrite DNS that already points at this tunnel, and does not edit any other ingress rule. The site check does not look up the new hostname. It connects to addresses of `pancakeboard.com`, which are the same Cloudflare edge, and requests `datum.pancakeboard.com` there, so a negative cache for the new name cannot hide an edge that is already serving it. It accepts only HTTP 200 whose body is the Datum page. The deploy prints the other tunnels' remaining ingress rules, including a hop rule when one is present, and does not write that config when the hostname is already gone.

The only one-time setup for the tunnel is the GitHub secrets below. There is no Cloudflare dashboard step and no token file to copy onto the server.

Bootstrap does not change sshd or the firewall, and it does not write another account's files.

### Config

Copy `deploy/deploy.env.example` to `deploy/deploy.env`. That file is shell assignments. Do not commit the filled file.

- `DEPLOY_HOST` — server hostname or IPv4 address
- `DEPLOY_USER` — `datum`
- `DEPLOY_SSH_PORT` — `4041`. SSH stays on that port. This install does not change sshd.
- `DEPLOY_SSH_KEY` — path to the datum private key
- `DEPLOY_SSH_KNOWN_HOSTS` — path to a `known_hosts` line for that host and port
- `CLOUDFLARE_API_TOKEN` — token with Account Cloudflare Tunnel Edit, and Zone DNS Edit for pancakeboard.com
- `CLOUDFLARE_ACCOUNT_ID` — the account that owns the tunnel
- `CLOUDFLARE_ZONE_ID` — the zone for pancakeboard.com
- `SMTP_PASSWORD` — the Google app password for `fig@mail.grokbot.com`. The release workflow sets this from the repository secret.

Copy `deploy/copy.defaults.example` to `deploy/copy.defaults` for `./deploy/copy.sh`. `SSH_USER` stays `datum`. `SSH_PORT` stays `4041`. Do not commit the filled file.

On the server, the mailbox password is the Podman secret `datum-smtp`, not a file. Deploy deletes a leftover `/home/datum/form.env`. The tunnel token is the Podman secret `datum-cloudflared`. Deploy deletes a leftover `/home/datum/tunnel.env`. Mail is STARTTLS on port 587 to `smtp.gmail.com`. The page still shows `hello@datum.studio`.

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
- `/home/datum/incoming/pod/datum.pod`
- `/home/datum/incoming/pod/datum-web.container`
- `/home/datum/incoming/pod/datum-brief.container`
- `/home/datum/incoming/pod/nginx.conf`
- `/home/datum/incoming/public/` (`index.html`, `css/`, `js/`, `assets/`)
- `/home/datum/incoming/app/brief.py`

`install` moves the pod files to `/home/datum/nginx.conf` and `/home/datum/.config/containers/systemd/`. It deletes a leftover `/home/datum/form.env`. When `current` is missing it creates `/home/datum/releases/initial`, points `current` at it, and copies `public/` and `app/brief.py` into that release. A later `./deploy/copy.sh` leaves a release that already has `brief.py`. It deletes `/home/datum/incoming/` and restarts `datum-pod`, `datum-web`, and `datum-brief`. The first start pulls `docker.io/library/nginx:1.28-alpine` and `docker.io/library/python:3.12-slim`. Run `./deploy/copy.sh` again when nginx or those quadlets change. It does not install the tunnel and it does not store the mailbox password. The release does.

`./deploy/deploy.sh` builds `public/` (`index.html`, `css/`, `js/`, `assets/` from the repository root) and `app/brief.py` (from `server/brief.py`), then `scp`s those two directories to `/home/datum/releases/<timestamp>/`. It also `scp`s `deploy/pod/datum-cloudflared.container` and `deploy/pod/datum-brief.container` to `/home/datum/.config/containers/systemd/`. Quadlet runs each container from its own unit file, so the host keeps `datum.pod`, `datum-web.container`, `datum-brief.container`, and `datum-cloudflared.container` there, plus `/home/datum/nginx.conf`. `Pod=datum.pod` is what puts cloudflared in the site pod and brings it back after a reboot. The token and the mailbox password are not files. The script stops the pod, deletes a leftover `/home/datum/tunnel.env`, deletes a leftover `/home/datum/form.env`, and deletes the old `datum-tunnel` unit. It runs `podman secret create --replace datum-cloudflared` and `podman secret create --replace datum-smtp`, then starts `datum-pod`, `datum-web`, `datum-brief`, and `datum-cloudflared`. It then waits until the tunnel is healthy, points DNS, and removes only the `datum.pancakeboard.com` rule from the other tunnel. The first deploy pulls `docker.io/cloudflare/cloudflared:2026.10.4`. If the new brief process does not answer `GET /health`, the script restores the previous release and does not move DNS.

```bash
./deploy/deploy.sh
```

A published GitHub release runs that same deploy. The release stores the mailbox password as the Podman secret `datum-smtp` (`SMTP_PASSWORD` in the brief container) and the connector token as the Podman secret `datum-cloudflared` (`TUNNEL_TOKEN` in the container). Deploy deletes a leftover `/home/datum/form.env` and a leftover `/home/datum/tunnel.env`.

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

That copies the page and `datum-cloudflared.container`, stops the pod, stores the tunnel token with `podman secret create --replace`, points `current` at the release, and starts `datum-pod`, `datum-web`, `datum-brief`, and `datum-cloudflared`. It deletes a leftover `/home/datum/tunnel.env` and the old `datum-tunnel` unit. It then waits until the tunnel is healthy, points DNS, and removes only the `datum.pancakeboard.com` ingress rule from the other tunnel. If the new brief process does not answer `GET /health`, the script points `current` back at the previous release and does not move DNS.

```bash
./deploy/deploy.sh rollback
```

Rollback swaps `current` and `previous`, then stops and starts `datum-pod`, `datum-web`, `datum-brief`, and `datum-cloudflared`. The five newest releases are kept, and the current and previous releases are not deleted. Rollback also deletes a leftover `/home/datum/tunnel.env` and the old `datum-tunnel` unit if they are still there. It does not replace the Podman secret, it does not change DNS, and it does not need the Cloudflare secrets.

## Release

Publishing a GitHub release deploys that tag. The workflow is `.github/workflows/release.yml`. It checks out the release tag and runs `./deploy/deploy.sh` on a GitHub-hosted runner, and only for `Ricodey/engineering-consulting`. The job SSHes in as `datum`.

### Secrets

Repository secrets, under Settings → Secrets and variables → Actions. None of these belong in the repo.

- `DEPLOY_HOST` — the server address
- `DEPLOY_USER` — `datum`
- `DEPLOY_SSH_PORT` — the SSH port already in use
- `DEPLOY_SSH_KEY` — the private key that matches the bootstrap public key, including the BEGIN and END lines
- `DEPLOY_SSH_KNOWN_HOSTS` — the server host key line for `DEPLOY_HOST` and `DEPLOY_SSH_PORT`
- `CLOUDFLARE_API_TOKEN` — Account Cloudflare Tunnel Edit, and Zone DNS Edit for pancakeboard.com
- `CLOUDFLARE_ACCOUNT_ID` — the account id
- `CLOUDFLARE_ZONE_ID` — the zone id for pancakeboard.com
- `SMTP_PASSWORD` — the Google app password for `fig@mail.grokbot.com`

The workflow passes `SMTP_PASSWORD` to `deploy.sh`. Deploy stores it as the Podman secret `datum-smtp` (`SMTP_PASSWORD` in the brief container) and deletes a leftover `/home/datum/form.env`. It fetches the connector token during the deploy and stores it as the Podman secret `datum-cloudflared` (`TUNNEL_TOKEN` in the container). Deploy deletes a leftover `/home/datum/tunnel.env`. That token is not a repository secret and it is not committed. Mail is STARTTLS on port 587 to `smtp.gmail.com`, from and to `fig@mail.grokbot.com`, with Reply-To set to the submitter. SSH stays on port 4041. The pod publishes `127.0.0.1:8443` only. Nothing listens on port 80 or 443.

The job writes those values into `deploy/deploy.env` for `deploy.sh`, then deletes that file and the key. If the new brief process does not answer `GET /health`, `deploy.sh` points `current` back at the previous release and does not change DNS. If the cutover fails after DNS has moved, the job fails and leaves the release in place; run it again. Moving the pod to another server is a new `DEPLOY_HOST` and the same install.
