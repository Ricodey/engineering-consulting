# Agents

This file is the working agreement for engineering-consulting (Datum). The site is a static page plus a form handler that emails a project brief. `README.md` has the same commands with the hosting details.

## Run

On the computer where you edit the site. Do not deploy to preview a change. From the repository root:

```bash
python3 server/preview.py
```

Open http://127.0.0.1:4173/. The process serves the page and `POST /api/brief`. It reads the page files on each request. It does not read `deploy/form.env`. A valid brief returns 503 until SMTP is in the environment. Reload the browser after an `index.html`, `css/`, `js/`, or `assets/` edit. Restart the process after a `server/brief.py` or `server/preview.py` edit.

`datum-site.service` may already listen on `127.0.0.1:4173`. Restart that unit instead of starting a second process. The unit file is `~/.config/systemd/user/datum-site.service` and is not in this repository.

```bash
systemctl --user restart datum-site.service
```

`server/brief.py` is the process the pod runs. It listens on `127.0.0.1:4174`. `GET /health` is the readiness check. Mail is STARTTLS on port 587. Copy `deploy/form.env.example` to `deploy/form.env`, fill it in, and do not commit it. Source that file only when you run `python3 server/brief.py` yourself.

## Test

There is no compile step.

```bash
python3 -m unittest discover -s server -v
bash -n deploy/deploy.sh deploy/datum.sh deploy/copy.sh deploy/stage.sh
./deploy/deploy.sh --dry-run
python3 -m pip install ruff==0.17.2
ruff check .
ruff format --check .
shellcheck deploy/deploy.sh deploy/datum.sh deploy/copy.sh deploy/stage.sh
shfmt -d deploy/deploy.sh deploy/datum.sh deploy/copy.sh deploy/stage.sh
node --check js/main.js
```

The pod tests run `nginx -t` on `deploy/pod/nginx.conf`. Install nginx first if it is not already there.

`./deploy/deploy.sh --dry-run` only stages a release locally. Pull requests run these checks in `.github/workflows/ci.yml`.

## Deploy

The site is a rootless Podman pod for user `datum`. It publishes `127.0.0.1:8443` and no other port. nginx inside the pod listens on port 8080. `datum-cloudflared` is `docker.io/cloudflare/cloudflared:2026.10.4` in that pod. It runs `tunnel --no-autoupdate run` and dials `http://127.0.0.1:8080`. The connector token is the Podman secret `datum-cloudflared`, read as `TUNNEL_TOKEN`. Deploy deletes a leftover `/home/datum/tunnel.env`. The public name is `https://datum.pancakeboard.com/`. Bootstrap does not change sshd or the firewall.

A release ensures the remotely managed tunnel named `datum`, stores the token with `podman secret create --replace`, and replaces the pod. It waits until the tunnel is healthy, then points `datum.pancakeboard.com` at it, then removes only that hostname's ingress rule from any other tunnel. Other rules stay, and the deploy prints them. The other tunnel is not deleted. The site check connects to addresses of `pancakeboard.com` and requests the new hostname there, so a negative cache for that name cannot hide the edge. It accepts only HTTP 200 with the Datum page. A re-run does not create a second tunnel, does not rewrite DNS that already points here, and does not edit any other rule.

The only one-time setup for the tunnel is repository secrets `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, and `CLOUDFLARE_ZONE_ID`. The token needs Account Cloudflare Tunnel Edit, and Zone DNS Edit for pancakeboard.com.

```bash
# On the server, as root, once. Root SSH stays closed. DEPLOY_AUTHORIZED_KEY is the one datum public key line.
curl -fsSL -o /tmp/datum.sh https://raw.githubusercontent.com/Ricodey/engineering-consulting/main/deploy/datum.sh
DEPLOY_AUTHORIZED_KEY='ssh-ed25519 AAAA... datum-deploy' bash /tmp/datum.sh bootstrap
rm -f /tmp/datum.sh
```

Bootstrap installs the distro packages `podman` and `uidmap` when the `podman` command is missing, and leaves an existing Podman in place. Quadlets need Podman 4.4 or newer.

Then, from a checkout, `./deploy/copy.sh` uploads the pod, nginx config, `form.env`, the page, and `server/brief.py` to `/home/datum/incoming/` as `datum` and runs `datum.sh install` on that login. The first install copies the page and `brief.py` into `/home/datum/releases/initial`. `install` does not start the tunnel.

`deploy.sh` uses `DEPLOY_HOST`, `DEPLOY_USER=datum`, and `DEPLOY_SSH_PORT` from `deploy/deploy.env`, plus `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, and `CLOUDFLARE_ZONE_ID`. It `scp`s `datum-cloudflared.container`, stops the pod, deletes a leftover `/home/datum/tunnel.env` and the old `datum-tunnel` unit, stores the token with `podman secret create --replace`, `scp`s the page and `server/brief.py` into `/home/datum/releases/<timestamp>/`, and starts `datum-pod`, `datum-web`, `datum-brief`, and `datum-cloudflared`. It then cuts DNS over. Rollback does not call Cloudflare and does not replace the secret. The host still has the pod quadlet and the web, brief, and cloudflared container units, because that is how Quadlet runs containers in a pod, plus `form.env` and `nginx.conf`.

```bash
./deploy/deploy.sh
./deploy/deploy.sh rollback
```

`bash deploy/datum.sh remove`, as root, deletes the `datum` user and `/home/datum`. `README.md` has the tunnel hostname and what rollback keeps. Do not put passwords, private keys, or filled env files in a commit.

## Release

Publishing a release deploys that tag. `.github/workflows/release.yml` checks out the tag and runs `./deploy/deploy.sh` on a GitHub-hosted runner. The job SSHes in as `datum` for `Ricodey/engineering-consulting`.

Repository secrets `DEPLOY_HOST`, `DEPLOY_USER` (`datum`), `DEPLOY_SSH_PORT`, `DEPLOY_SSH_KEY`, `DEPLOY_SSH_KNOWN_HOSTS`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, and `CLOUDFLARE_ZONE_ID` are what the workflow passes to `deploy.sh`. `CLOUDFLARE_API_TOKEN` needs Account Cloudflare Tunnel Edit, and Zone DNS Edit for pancakeboard.com. SMTP stays in `/home/datum/form.env`. The connector token is stored as a Podman secret during the deploy (`TUNNEL_TOKEN` in the container). Deploy deletes a leftover `/home/datum/tunnel.env`. Run the root bootstrap, then `./deploy/copy.sh`, before the first release.

## Conventions

- `server/brief.py` stays on the Python 3 standard library. Do not add a dependency, database, or queue. The tunnel is the pinned `datum-cloudflared` container in the site pod, not another application service.
- The page stays at the repository root (`index.html`, `css/`, `js/`, `assets/`). `deploy/deploy.sh` copies those paths into `public/` and copies `server/brief.py` to `app/brief.py`.
- Tests are `unittest` in `server/test_brief.py`, `server/test_prefix.py`, `server/test_preview.py`, `server/test_runner.py`, `server/test_tunnel.py`, and `server/test_secret.py`.
- Python lint is Ruff (`ruff.toml`, 0.17.2 in CI). Shell lint is ShellCheck (`.shellcheckrc`) and shfmt (`.editorconfig`) on the scripts in `deploy/`. HTML, CSS, and JS follow `.editorconfig` and are not run through a formatter.
- Target Python 3.10 or newer. That is Ubuntu 22.04's `python3`, which CI uses.
- Secrets stay in `deploy/deploy.env`, `deploy/copy.defaults`, `deploy/form.env`, and on the server in `/home/datum/form.env`. The connector token is the Podman secret `datum-cloudflared` (`TUNNEL_TOKEN` in the container). Deploy deletes a leftover `/home/datum/tunnel.env`. The Cloudflare token, account id, and zone id are repository secrets `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, and `CLOUDFLARE_ZONE_ID`.

## Standing rules

- This repo only (engineering-consulting).
- No backwards compatibility, shims, or fallbacks unless Benjamin explicitly asks.
- Pull request titles and descriptions must match the code that lands.
- Scratch worktrees only under `~/.grok/worktrees/` or `/tmp`, never `~/projects`, and remove them when done.
- Open pull requests ready for review, not as drafts.
- Never merge without Benjamin's explicit approval.
- New work bases on the repo default branch (`main`) unless it is explicitly stacked on an open pull request.
- The site is a page plus forms that email Benjamin. Keep the backend minimal.
- Keep the current front-end design unless Benjamin asks to change it.
- One pull request per concern where practical.
