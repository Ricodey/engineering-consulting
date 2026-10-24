import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
DATUM = ROOT / "deploy" / "datum.sh"
COPY = ROOT / "deploy" / "copy.sh"
POD = ROOT / "deploy" / "pod"
NGINX = POD / "nginx.conf"


def run(script, *args, env):
    return subprocess.run(
        ["bash", str(script), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def values_of(result):
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    parsed = {}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed[key] = value
    return parsed


class PodContractTests(unittest.TestCase):
    def test_quadlet_publishes_only_loopback(self):
        pod = (POD / "datum.pod").read_text(encoding="utf-8")
        web = (POD / "datum-web.container").read_text(encoding="utf-8")
        brief = (POD / "datum-brief.container").read_text(encoding="utf-8")
        self.assertIn("PodName=datum", pod)
        self.assertIn("PublishPort=127.0.0.1:8443:8080", pod)
        self.assertEqual(pod.count("PublishPort="), 1)
        self.assertNotIn("PublishPort=", web)
        self.assertNotIn("PublishPort=", brief)
        self.assertIn("Image=docker.io/library/nginx:1.28-alpine", web)
        self.assertIn("Image=docker.io/library/python:3.12-slim", brief)
        self.assertIn("Volume=/home/datum/current/public:/usr/share/nginx/html:ro,Z", web)
        self.assertIn("Volume=/home/datum/current:/srv:ro,Z", brief)
        self.assertIn("EnvironmentFile=/home/datum/form.env", brief)
        for unit in (pod, web, brief):
            self.assertIn("WantedBy=default.target", unit)
        self.assertIn("TimeoutStartSec=300", web)
        self.assertIn("TimeoutStartSec=300", brief)
        self.assertNotIn("/home/pancake", pod + web + brief)
        self.assertNotIn("/etc/pancake", pod + web + brief)
        self.assertNotIn("/var/www", pod + web + brief)

    def test_nginx_serves_the_site_root_and_the_brief(self):
        config = NGINX.read_text(encoding="utf-8")
        self.assertIn("listen 8080;", config)
        self.assertIn("location = /api/brief {", config)
        self.assertIn("proxy_pass http://127.0.0.1:4174/api/brief;", config)
        self.assertIn("proxy_set_header X-Real-IP $datum_client_ip;", config)
        self.assertIn("$http_cf_connecting_ip", config)
        self.assertNotIn("proxy_set_header X-Real-IP $remote_addr;", config)
        self.assertIn("location = /health {", config)
        self.assertIn("proxy_pass http://127.0.0.1:4174/health;", config)
        self.assertIn("root /usr/share/nginx/html;", config)
        self.assertNotRegex(config, r"listen\s+443")
        self.assertNotRegex(config, r"listen\s+80;")
        self.assertNotRegex(config, r"listen\s+\[::\]:80;")
        self.assertNotRegex(config, r"listen\s+8443")
        self.assertNotIn("/consulting", config)

    def test_nginx_config_parses(self):
        try:
            nginx = subprocess.run(["nginx", "-v"], capture_output=True, text=True, check=False)
        except FileNotFoundError:
            self.skipTest("nginx is not installed")
        self.assertEqual(nginx.returncode, 0, nginx.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            self.assert_nginx_parses(Path(tmp))

    def assert_nginx_parses(self, root):
        for name in ("body", "proxy", "fastcgi", "uwsgi", "scgi"):
            (root / name).mkdir()
        wrapper = root / "nginx.conf"
        wrapper.write_text(
            "\n".join(
                [
                    "worker_processes 1;",
                    "error_log %s/error.log;" % root,
                    "pid %s/nginx.pid;" % root,
                    "events { worker_connections 64; }",
                    "http {",
                    "    access_log %s/access.log;" % root,
                    "    client_body_temp_path %s/body;" % root,
                    "    proxy_temp_path %s/proxy;" % root,
                    "    fastcgi_temp_path %s/fastcgi;" % root,
                    "    uwsgi_temp_path %s/uwsgi;" % root,
                    "    scgi_temp_path %s/scgi;" % root,
                    "    include /etc/nginx/mime.types;",
                    "    include %s;" % NGINX,
                    "}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        result = subprocess.run(
            ["nginx", "-t", "-c", str(wrapper)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_bootstrap_dry_run_avoids_the_pancake_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            subuid = Path(tmp) / "subuid"
            subgid = Path(tmp) / "subgid"
            subuid.write_text("pancake:100000:65536\n", encoding="utf-8")
            subgid.write_text("pancake:100000:65536\n", encoding="utf-8")
            env = os.environ.copy()
            env["DATUM_SUBUID_FILE"] = str(subuid)
            env["DATUM_SUBGID_FILE"] = str(subgid)
            before = subuid.read_text(encoding="utf-8")
            result = run(DATUM, "bootstrap", "--dry-run", env=env)
            parsed = values_of(result)
            start = int(parsed["subuid_start"])
            size = int(parsed["subuid_size"])
            self.assertEqual(parsed["user"], "datum")
            self.assertEqual(parsed["home"], "/home/datum")
            self.assertEqual(size, 65536)
            self.assertFalse(start < 165536 and start + size > 100000)
            self.assertGreaterEqual(start, 165536)
            self.assertEqual(subuid.read_text(encoding="utf-8"), before)
            self.assertNotIn("useradd", result.stdout)
            self.assertNotIn("apt-get", result.stdout)

    def test_remove_dry_run_lists_only_datum(self):
        result = run(DATUM, "remove", "--dry-run", env=os.environ.copy())
        parsed = values_of(result)
        self.assertEqual(parsed["user"], "datum")
        self.assertEqual(parsed["home"], "/home/datum")
        self.assertNotIn("/home/pancake", result.stdout)
        self.assertNotIn("/etc/pancake", result.stdout)
        script = DATUM.read_text(encoding="utf-8")
        self.assertIn('die "refusing to change $path"', script)
        self.assertIn("/home/pancake", script)
        self.assertIn("/etc/pancake", script)

    def test_deploy_restarts_only_datum_units(self):
        deploy = (ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn("/home/datum", deploy)
        self.assertIn("DEPLOY_USER must be datum", deploy)
        self.assertIn("datum-*)", deploy)
        self.assertIn("refusing unit", deploy)
        self.assertIn("datum-web datum-brief", deploy)
        self.assertNotIn("/var/www/datum", deploy)
        self.assertNotIn("datum-brief.service", deploy.split("restart_units", 1)[0])
        self.assertNotIn("ufw", deploy)
        self.assertNotIn("sshd", deploy)
        self.assertIn("flock -w 120 9", deploy)
        self.assertIn("StrictHostKeyChecking=yes", deploy)
        self.assertNotIn("accept-new", deploy)
        self.assertNotIn("ast.parse", deploy)
        self.assertNotIn("rsync", deploy)
        self.assertIn('"$stage/public"', deploy)
        self.assertIn('"$stage/app"', deploy)
        self.assertIn("run ./deploy/copy.sh", deploy)
        install = (ROOT / "deploy" / "datum.sh").read_text(encoding="utf-8")
        self.assertIn('if [ -f "$home/current/app/brief.py" ]', install)
        self.assertIn("apt-get install -y podman uidmap", install)
        self.assertIn("podman already installed", install)
        self.assertIn("quadlets need 4.4 or newer", install)
        self.assertIn("DEPLOY_AUTHORIZED_KEY", install)
        self.assertNotIn("DEPLOY_AUTHORIZED_KEY_FILE", install)
        self.assertIn("systemctl --user restart datum-pod.service datum-web.service", install)
        self.assertNotIn("apt-get upgrade", install)
        self.assertNotIn("apt-get remove", install)
        self.assertNotIn("ufw", install)

    def test_copy_targets_only_the_datum_home(self):
        script = COPY.read_text(encoding="utf-8")
        self.assertIn("dest=/home/datum/incoming", script)
        self.assertIn("SSH_USER must be datum", script)
        self.assertIn("bash ${dest}/datum.sh install", script)
        self.assertIn(
            "https://raw.githubusercontent.com/Ricodey/engineering-consulting/main/deploy/datum.sh",
            script,
        )
        self.assertNotIn("pancake", script)
        self.assertNotIn("accept-new", script)
        self.assertNotIn("DEPLOY_AUTHORIZED_KEY_FILE", script)
        example = (ROOT / "deploy" / "copy.defaults.example").read_text(encoding="utf-8")
        self.assertIn("SSH_USER=datum", example)
        self.assertIn("HOST=\n", example)
        self.assertNotIn("118.127.40.148", example)
        self.assertNotIn("/home/robot", example)
        with tempfile.TemporaryDirectory() as tmp:
            defaults = Path(tmp) / "copy.defaults"
            defaults.write_text(
                "HOST=198.51.100.10\nSSH_PORT=4041\nSSH_USER=datum\nSSH_KEY=/tmp/key\nFORM_ENV=/tmp/form.env\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["COPY_DEFAULTS"] = str(defaults)
            result = run(COPY, "--print-plan", env=env)
            parsed = values_of(result)
            self.assertEqual(parsed["ssh_user"], "datum")
            self.assertEqual(parsed["dest"], "/home/datum/incoming")
            self.assertEqual(parsed["host"], "198.51.100.10")
            self.assertEqual(parsed["port"], "4041")

    def test_bootstrap_checks_the_authorized_key_before_root(self):
        if os.geteuid() == 0:
            self.skipTest("bootstrap would create the datum user as root")
        missing = os.environ.copy()
        missing.pop("DEPLOY_AUTHORIZED_KEY", None)
        result = run(DATUM, "bootstrap", env=missing)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("set DEPLOY_AUTHORIZED_KEY", result.stderr)
        self.assertNotIn("useradd", result.stdout)

        rejected = os.environ.copy()
        rejected["DEPLOY_AUTHORIZED_KEY"] = "not-a-key"
        result = run(DATUM, "bootstrap", env=rejected)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not an OpenSSH public key", result.stderr)

        duplicate = os.environ.copy()
        duplicate["DEPLOY_AUTHORIZED_KEY"] = (
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyOnlyForTheParser one\n"
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyOnlyForTheParser two\n"
        )
        result = run(DATUM, "bootstrap", env=duplicate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must contain one public key", result.stderr)

        accepted = os.environ.copy()
        accepted["DEPLOY_AUTHORIZED_KEY"] = (
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyOnlyForTheParser datum-deploy"
        )
        result = run(DATUM, "bootstrap", env=accepted)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("run bootstrap as root", result.stderr)
        self.assertIn("user=datum", result.stdout)
        self.assertNotIn("useradd", result.stdout)

    def test_copy_rejects_another_ssh_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            defaults = Path(tmp) / "copy.defaults"
            defaults.write_text(
                "HOST=198.51.100.10\nSSH_PORT=4041\nSSH_USER=other\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["COPY_DEFAULTS"] = str(defaults)
            result = run(COPY, "--print-plan", env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SSH_USER must be datum", result.stderr)


class PageUrlTests(unittest.TestCase):
    def test_page_urls_stay_relative_to_the_document(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "css" / "styles.css").read_text(encoding="utf-8")
        js = (ROOT / "js" / "main.js").read_text(encoding="utf-8")
        for line in html.splitlines():
            self.assertNotIn('href="/', line)
            self.assertNotIn('src="/', line)
        self.assertNotIn("url(/", css)
        self.assertIn('fetch("api/brief"', js)
        self.assertNotIn('fetch("/', js)
        self.assertEqual(
            urljoin("https://datum.pancakeboard.com/", "api/brief"),
            "https://datum.pancakeboard.com/api/brief",
        )
        self.assertEqual(
            urljoin("https://datum.pancakeboard.com/", "css/styles.css"),
            "https://datum.pancakeboard.com/css/styles.css",
        )

    def test_readme_documents_the_test_hostname(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for doc in (readme, agents):
            self.assertIn("datum.pancakeboard.com", doc)
            self.assertIn("127.0.0.1:8443", doc)
            self.assertIn("datum.sh bootstrap", doc)
            self.assertIn(
                "https://raw.githubusercontent.com/Ricodey/engineering-consulting/main/deploy/datum.sh",
                doc,
            )
            self.assertIn("DEPLOY_AUTHORIZED_KEY=", doc)
            self.assertNotIn("DEPLOY_AUTHORIZED_KEY_FILE", doc)
            self.assertNotIn("ssh -p 4041", doc)
            self.assertNotIn("setup-server.sh", doc)
            self.assertNotIn("setup-runner.sh", doc)
        self.assertIn("/home/datum", readme)
        self.assertIn("/home/datum/incoming/", readme)
        self.assertIn("/home/datum/releases/<timestamp>/", readme)
        self.assertIn("`podman` and `uidmap`", readme)
        self.assertIn("docker.io/library/nginx:1.28-alpine", readme)
        self.assertIn("docker.io/library/python:3.12-slim", readme)
        self.assertIn("does not change sshd or the firewall", agents)
        self.assertIn("`podman` and `uidmap`", agents)
