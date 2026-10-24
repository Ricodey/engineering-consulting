# Agents

This file is the working agreement for engineering-consulting (Datum). The site is a static page plus a form handler that emails a project brief. `README.md` has the same commands with the hosting details.

## Run

Page, from the repository root. This preview does not accept the form:

```bash
python3 -m http.server 4173 --bind 127.0.0.1
```

Form handler. Copy `deploy/form.env.example` to `deploy/form.env`, fill it in, and do not commit it:

```bash
set -a
. deploy/form.env
set +a
python3 server/brief.py
```

The handler listens on `127.0.0.1:4174`. `GET /health` is the readiness check. Mail is STARTTLS on port 587.

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

The site is a rootless Podman pod for user `datum`. It publishes `127.0.0.1:8443` and no other port. The test URL is `https://datum.pancakeboard.com/`, added as a public hostname on the tunnel that is already running, service `http://127.0.0.1:8443`. Bootstrap does not change sshd or the firewall.

```bash
# On the server, as root, once. Root SSH stays closed. DEPLOY_AUTHORIZED_KEY is the one datum public key line.
curl -fsSL -o /tmp/datum.sh https://raw.githubusercontent.com/Ricodey/engineering-consulting/main/deploy/datum.sh
DEPLOY_AUTHORIZED_KEY='ssh-ed25519 AAAA... datum-deploy' bash /tmp/datum.sh bootstrap
rm -f /tmp/datum.sh
```

Bootstrap installs the distro packages `podman` and `uidmap` when the `podman` command is missing, and leaves an existing Podman in place. Quadlets need Podman 4.4 or newer.

Then, from a checkout, `./deploy/copy.sh` uploads the pod, nginx config, `form.env`, the page, and `server/brief.py` to `/home/datum/incoming/` as `datum` and runs `datum.sh install` on that login. The first install copies the page and `brief.py` into `/home/datum/releases/initial`.

`deploy.sh` uses `DEPLOY_HOST`, `DEPLOY_USER=datum`, and `DEPLOY_SSH_PORT` from `deploy/deploy.env`. It `scp`s the page and `server/brief.py` into `/home/datum/releases/<timestamp>/`. A release runs that upload and leaves `form.env` and the quadlets as `install` wrote them.

```bash
./deploy/deploy.sh
./deploy/deploy.sh rollback
```

`bash deploy/datum.sh remove`, as root, deletes the `datum` user and `/home/datum`. `README.md` has the tunnel hostname and what rollback keeps. Do not put passwords, private keys, or filled env files in a commit.

## Release

Publishing a release deploys that tag. `.github/workflows/release.yml` checks out the tag and runs `./deploy/deploy.sh` on a GitHub-hosted runner. The job SSHes in as `datum` for `Ricodey/engineering-consulting`.

Repository secrets `DEPLOY_HOST`, `DEPLOY_USER` (`datum`), `DEPLOY_SSH_PORT`, `DEPLOY_SSH_KEY`, and `DEPLOY_SSH_KNOWN_HOSTS` are what the workflow passes to `deploy.sh`. SMTP stays in `/home/datum/form.env`. Run the root bootstrap, then `./deploy/copy.sh`, before the first release.

## Conventions

- `server/brief.py` stays on the Python 3 standard library. Do not add a dependency, database, queue, or extra service.
- The page stays at the repository root (`index.html`, `css/`, `js/`, `assets/`). `deploy/deploy.sh` copies those paths into `public/` and copies `server/brief.py` to `app/brief.py`.
- Tests are `unittest` in `server/test_brief.py` and `server/test_prefix.py`.
- Python lint is Ruff (`ruff.toml`, 0.17.2 in CI). Shell lint is ShellCheck (`.shellcheckrc`) and shfmt (`.editorconfig`) on the scripts in `deploy/`. HTML, CSS, and JS follow `.editorconfig` and are not run through a formatter.
- Target Python 3.10 or newer. That is Ubuntu 22.04's `python3`, which CI uses.
- Secrets stay in `deploy/deploy.env`, `deploy/copy.defaults`, `deploy/form.env`, and `/home/datum/form.env`.

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
