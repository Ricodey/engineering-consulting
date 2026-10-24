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
bash -n deploy/deploy.sh deploy/setup-server.sh deploy/switch.sh
./deploy/deploy.sh --dry-run
python3 -m pip install ruff==0.17.2
ruff check .
ruff format --check .
shellcheck deploy/deploy.sh deploy/setup-server.sh deploy/switch.sh
shfmt -d deploy/deploy.sh deploy/setup-server.sh deploy/switch.sh
node --check js/main.js
```

The prefix tests run `sudo nginx -t` (it binds the listen port). Install nginx first if it is not already there.

`./deploy/deploy.sh --dry-run` only stages a release locally. Pull requests run these checks in `.github/workflows/ci.yml`.

## Deploy

Debian 12 or Ubuntu 22.04+. The host, domain, Cloudflare origin certificate, and SMTP account are supplied outside this repository. Nothing here creates DNS records. nginx is HTTPS only. Cloudflare handles the HTTP-to-HTTPS redirect (Always Use HTTPS, SSL mode Full strict).

`SITE_PREFIX=/` (the default) is a dedicated host: nginx on 443, sshd on 4041, firewall allows 4041 and 443. Any other prefix is a shared host: nginx on 8443 only, and setup does not change sshd or the firewall. The test values are `SITE_DOMAIN=pancakeboard.com` and `SITE_PREFIX=/consulting`. `README.md` has the Cloudflare origin rule for that path. Do not run the dedicated command on the shared machine.

```bash
# Dedicated host. SITE_PREFIX=/ may be omitted.
SITE_DOMAIN=datum.studio \
SITE_PREFIX=/ \
DEPLOY_AUTHORIZED_KEY_FILE=/root/deploy.pub \
  bash deploy/setup-server.sh

# Shared host. Same script. Does not bind 443 or edit the other service.
SITE_DOMAIN=pancakeboard.com \
SITE_PREFIX=/consulting \
DEPLOY_AUTHORIZED_KEY_FILE=/root/deploy.pub \
  bash deploy/setup-server.sh
```

Run that on the server as root, once the origin certificate is in place. `deploy.sh` uses `DEPLOY_SSH_PORT` from `deploy/deploy.env` (4041 on a dedicated host; the existing SSH port on a shared host). Then, from a checkout with `deploy/deploy.env` filled in:

```bash
./deploy/deploy.sh
./deploy/deploy.sh rollback
```

`README.md` lists the certificate paths, `/etc/datum/form.env`, the firewall, and what rollback keeps. Do not put passwords, private keys, or filled env files in a commit.

## Conventions

- `server/brief.py` stays on the Python 3 standard library. Do not add a dependency, database, queue, or extra service.
- The page stays at the repository root (`index.html`, `css/`, `js/`, `assets/`). `deploy/deploy.sh` copies those paths into `public/` and copies `server/brief.py` to `app/brief.py`.
- Tests are `unittest` in `server/test_brief.py` and `server/test_prefix.py`.
- Python lint is Ruff (`ruff.toml`, 0.17.2 in CI). Shell lint is ShellCheck (`.shellcheckrc`) and shfmt (`.editorconfig`) on the three scripts in `deploy/`. HTML, CSS, and JS follow `.editorconfig` and are not run through a formatter.
- Target Python 3.10 or newer. That is Ubuntu 22.04's `python3`, which CI uses.
- Secrets stay in `deploy/deploy.env` and `/etc/datum/form.env`.

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
