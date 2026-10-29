import http.client
import os
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "deploy" / "raptorpath.sh"
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
    def test_quadlet_publishes_no_host_port(self):
        pod = (POD / "raptorpath.pod").read_text(encoding="utf-8")
        web = (POD / "raptorpath-web.container").read_text(encoding="utf-8")
        brief = (POD / "raptorpath-brief.container").read_text(encoding="utf-8")
        tunnel = (POD / "raptorpath-cloudflared.container").read_text(encoding="utf-8")
        self.assertFalse((POD / "raptorpath-tunnel.container").exists())
        self.assertIn("PodName=raptorpath", pod)
        self.assertNotIn("PublishPort=", pod)
        self.assertNotIn("PublishPort=", web)
        self.assertNotIn("PublishPort=", brief)
        self.assertNotIn("PublishPort=", tunnel)
        self.assertIn("Image=docker.io/library/nginx:1.28-alpine", web)
        self.assertIn("Image=docker.io/library/python:3.12-slim", brief)
        self.assertIn("Image=docker.io/cloudflare/cloudflared:2026.10.4", tunnel)
        self.assertIn("ContainerName=raptorpath-cloudflared", tunnel)
        self.assertIn("Pod=raptorpath.pod", tunnel)
        self.assertIn("Secret=raptorpath-cloudflared,type=env,target=TUNNEL_TOKEN", tunnel)
        self.assertNotIn("EnvironmentFile=", tunnel)
        self.assertNotIn("tunnel.env", tunnel)
        self.assertIn("Exec=tunnel --no-autoupdate run", tunnel)
        self.assertNotIn("--token", tunnel)
        self.assertNotIn("Network=host", tunnel)
        self.assertNotIn("TUNNEL_TOKEN=", tunnel)
        self.assertIn("Volume=/home/raptorpath/current/public:/usr/share/nginx/html:ro,Z", web)
        self.assertIn("Volume=/home/raptorpath/current:/srv:ro,Z", brief)
        self.assertIn("Secret=raptorpath-email,type=env,target=CF_EMAIL_TOKEN", brief)
        self.assertNotIn("EnvironmentFile=", brief)
        self.assertNotIn("form.env", brief)
        self.assertNotIn("SMTP_PASSWORD", brief)
        self.assertNotIn("smtp.gmail.com", brief)
        for unit in (pod, web, brief, tunnel):
            self.assertIn("WantedBy=default.target", unit)
        self.assertIn("TimeoutStartSec=300", web)
        self.assertIn("TimeoutStartSec=300", brief)
        self.assertIn("TimeoutStartSec=300", tunnel)
        joined = pod + web + brief + tunnel
        self.assertNotIn("/home/pancake", joined)
        self.assertNotIn("/etc/pancake", joined)
        self.assertNotIn("/var/www", joined)
        self.assertNotRegex(joined, r"(?i)listen\s+443")
        self.assertNotRegex(joined, r"(?i)listen\s+80")
        self.assertNotRegex(pod + tunnel, r":80\b")
        self.assertNotRegex(pod + tunnel, r":443\b")

    def test_nginx_serves_the_site_root_and_the_brief(self):
        config = NGINX.read_text(encoding="utf-8")
        self.assertIn("listen 8080;", config)
        self.assertIn("listen 8080 default_server;", config)
        self.assertIn("listen [::]:8080 default_server;", config)
        self.assertIn("location = /api/brief {", config)
        self.assertIn("proxy_pass http://127.0.0.1:4174/api/brief;", config)
        self.assertIn("proxy_set_header X-Real-IP $raptorpath_client_ip;", config)
        self.assertIn("$http_cf_connecting_ip", config)
        self.assertNotIn("proxy_set_header X-Real-IP $remote_addr;", config)
        self.assertIn("location = /health {", config)
        self.assertIn("proxy_pass http://127.0.0.1:4174/health;", config)
        self.assertIn("root /usr/share/nginx/html;", config)
        self.assertNotRegex(config, r"listen\s+443")
        self.assertNotRegex(config, r"listen\s+80;")
        self.assertNotRegex(config, r"listen\s+\[::\]:80;")
        self.assertNotIn("/consulting", config)
        self.assertIn("server_name www.raptorpath.com;", config)
        self.assertIn("return 301 https://raptorpath.com$request_uri;", config)

    def test_nginx_routes_apex_www_and_unknown_hosts(self):
        try:
            nginx = subprocess.run(["nginx", "-v"], capture_output=True, text=True, check=False)
        except FileNotFoundError:
            self.skipTest("nginx is not installed")
        self.assertEqual(nginx.returncode, 0, nginx.stderr)

        class HealthStub(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.split("?", 1)[0] != "/health":
                    self.send_error(404)
                    return
                body = b'{"ok": true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):
                return

        stub = ThreadingHTTPServer(("127.0.0.1", 4174), HealthStub)
        thread = threading.Thread(target=stub.serve_forever, daemon=True)
        thread.start()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assert_nginx_parses(root)
            wrapper = root / "nginx.conf"
            proc = subprocess.Popen(
                ["nginx", "-c", str(wrapper), "-g", "daemon off;"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            def request(host, path):
                error = None
                for _ in range(50):
                    try:
                        conn = http.client.HTTPConnection("127.0.0.1", 8080, timeout=1)
                        conn.request("GET", path, headers={"Host": host})
                        resp = conn.getresponse()
                        status = resp.status
                        location = resp.getheader("Location")
                        body = resp.read()
                        conn.close()
                        return status, location, body
                    except OSError as exc:
                        error = exc
                        time.sleep(0.05)
                err = ""
                if proc.stderr is not None:
                    err = proc.stderr.read().decode("utf-8", "replace")
                self.fail("%s\n%s" % (error, err))

            try:
                apex_status, apex_location, apex_body = request("raptorpath.com", "/")
                health_status, health_location, health_body = request("raptorpath.com", "/health")
                www_status, www_location, _www_body = request("www.raptorpath.com", "/x?y=1")
                unknown_status, unknown_location, _unknown_body = request(
                    "unknown.example", "/x?y=1"
                )
                self.assertEqual(apex_status, 200)
                self.assertIsNone(apex_location)
                self.assertGreater(len(apex_body), 0)
                self.assertEqual(health_status, 200)
                self.assertIsNone(health_location)
                self.assertIn(b'"ok": true', health_body)
                self.assertEqual(www_status, 301)
                self.assertEqual(www_location, "https://raptorpath.com/x?y=1")
                self.assertNotEqual(unknown_status, 301)
                self.assertIsNone(unknown_location)
            finally:
                proc.terminate()
                proc.wait(timeout=5)
                stub.shutdown()

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
            env["RAPTORPATH_SUBUID_FILE"] = str(subuid)
            env["RAPTORPATH_SUBGID_FILE"] = str(subgid)
            before = subuid.read_text(encoding="utf-8")
            result = run(SETUP, "bootstrap", "--dry-run", env=env)
            parsed = values_of(result)
            start = int(parsed["subuid_start"])
            size = int(parsed["subuid_size"])
            self.assertEqual(parsed["user"], "raptorpath")
            self.assertEqual(parsed["home"], "/home/raptorpath")
            self.assertEqual(size, 65536)
            self.assertFalse(start < 165536 and start + size > 100000)
            self.assertGreaterEqual(start, 165536)
            self.assertEqual(subuid.read_text(encoding="utf-8"), before)
            self.assertNotIn("useradd", result.stdout)
            self.assertNotIn("apt-get", result.stdout)
            self.assertNotIn("ufw", result.stdout)

    def test_remove_dry_run_lists_only_raptorpath(self):
        result = run(SETUP, "remove", "--dry-run", env=os.environ.copy())
        parsed = values_of(result)
        self.assertEqual(parsed["user"], "raptorpath")
        self.assertEqual(parsed["home"], "/home/raptorpath")
        self.assertNotIn("/home/pancake", result.stdout)
        self.assertNotIn("/etc/pancake", result.stdout)
        script = SETUP.read_text(encoding="utf-8")
        self.assertIn('die "refusing to change $path"', script)
        self.assertIn("/home/pancake", script)
        self.assertIn("/etc/pancake", script)

    def test_deploy_restarts_only_raptorpath_units(self):
        deploy = (ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn("/home/raptorpath", deploy)
        self.assertIn("DEPLOY_USER must be raptorpath", deploy)
        self.assertIn("raptorpath-*)", deploy)
        self.assertIn("refusing unit", deploy)
        self.assertIn(
            "raptorpath-pod raptorpath-web raptorpath-brief raptorpath-cloudflared", deploy
        )
        self.assertIn("raptorpath-cloudflared.container", deploy)
        self.assertIn("podman secret create --replace raptorpath-cloudflared -", deploy)
        self.assertIn("podman secret create --replace raptorpath-email -", deploy)
        self.assertNotIn("smtp.gmail.com", deploy)
        self.assertNotIn("SMTP_PASSWORD", deploy)
        self.assertIn("podman rm -f raptorpath-cloudflared", deploy)
        self.assertIn("podman rm -f raptorpath-brief", deploy)
        self.assertIn('rm -f "$base/form.env"', deploy)
        self.assertIn("unset CF_EMAIL_TOKEN", deploy)
        self.assertNotIn('echo "$CF_EMAIL_TOKEN"', deploy)
        self.assertIn(
            "declare -f link_to drop_old_tunnel drop_cloudflared_container drop_brief_container restart_units",
            deploy,
        )
        drain = deploy.split("drain)", 1)[1].split("switch)", 1)[0]
        self.assertIn("drop_cloudflared_container", drain)
        self.assertIn("drop_brief_container", drain)
        self.assertIn('rm -f "$base/tunnel.env"', deploy)
        self.assertIn(
            'rm -f "$base/.config/containers/systemd/raptorpath-tunnel.container"',
            deploy,
        )
        for line in deploy.splitlines():
            if "tunnel.env" in line or "raptorpath-tunnel.container" in line or "form.env" in line:
                self.assertIn("rm -f", line)
        self.assertIn(
            "systemctl --user start raptorpath-pod.service raptorpath-web.service raptorpath-brief.service raptorpath-cloudflared.service",
            deploy,
        )
        self.assertNotIn("/dev/stdin", deploy)
        self.assertNotIn("/var/www/raptorpath", deploy)
        self.assertNotIn("raptorpath-brief.service", deploy.split("restart_units", 1)[0])
        self.assertNotIn("sudo", deploy)
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
        self.assertLess(
            deploy.index("test -d ${DEPLOY_PATH}/releases"),
            deploy.index('cloudflare_tunnel.py" prepare'),
        )
        flow = deploy.split('echo "preparing the tunnel"', 1)[1]
        self.assertLess(
            flow.index("raptorpath-cloudflared.container"), flow.index('echo "draining"')
        )
        self.assertLess(flow.index("raptorpath-brief.container"), flow.index('echo "draining"'))
        self.assertLess(flow.index('"$ROOT/deploy/pod/nginx.conf"'), flow.index('echo "draining"'))
        self.assertLess(flow.index("${DEPLOY_PATH}/nginx.conf"), flow.index('echo "draining"'))
        nginx_conf = NGINX.read_text(encoding="utf-8")
        self.assertIn("return 301 https://raptorpath.com$request_uri;", nginx_conf)
        self.assertLess(flow.index('echo "draining"'), flow.index("install_tunnel_secret"))
        self.assertLess(flow.index("install_tunnel_secret"), flow.index("install_email_secret"))
        self.assertLess(flow.index("install_email_secret"), flow.index('echo "switching"'))
        self.assertLess(flow.index('echo "switching"'), flow.index('cloudflare_tunnel.py" finish'))
        self.assertLess(
            deploy.index('"$action" = "rollback"'), deploy.index("CLOUDFLARE_API_TOKEN")
        )
        self.assertLess(
            deploy.index('"$action" = "install-secret"'), deploy.index("deploy/deploy.env")
        )
        self.assertLess(
            deploy.index('"$action" = "install-email-secret"'), deploy.index("deploy/deploy.env")
        )
        self.assertNotIn("TUNNEL_ENV", deploy)
        self.assertNotIn("cat ", deploy)
        install = (ROOT / "deploy" / "raptorpath.sh").read_text(encoding="utf-8")
        install_head, install_tail = install.split("remove_site", 1)
        self.assertIn('if [ ! -f "$home/current/app/brief.py" ]', install)
        self.assertIn('"$incoming/app/brief.py"', install)
        self.assertIn('"$incoming/public/index.html"', install)
        self.assertNotIn("tunnel.env", install_head)
        self.assertNotIn("raptorpath-tunnel", install_head)
        self.assertNotIn("raptorpath-cloudflared.service", install_head)
        self.assertNotIn("raptorpath-cloudflared.container", install_head)
        self.assertIn("the first release will start the units", install_head)
        self.assertIn("podman secret inspect raptorpath-email", install_head)
        self.assertIn("podman secret inspect raptorpath-cloudflared", install_head)
        self.assertIn('rm -f "$home/form.env"', install)
        self.assertNotIn("incoming/form.env", install)
        self.assertIn(
            "systemctl --user restart raptorpath-pod.service raptorpath-web.service raptorpath-brief.service",
            install_head,
        )
        self.assertIn("raptorpath-tunnel.service", install_tail)
        self.assertIn("raptorpath-cloudflared.service", install_tail)
        self.assertIn("apt-get install -y podman uidmap", install)
        self.assertIn("podman already installed", install)
        self.assertIn("quadlets need 4.4 or newer", install)
        self.assertIn("DEPLOY_AUTHORIZED_KEY", install)
        self.assertNotIn("DEPLOY_AUTHORIZED_KEY_FILE", install)
        brief_unit = (POD / "raptorpath-brief.container").read_text(encoding="utf-8")
        self.assertNotIn("ConditionPathExists", brief_unit)
        self.assertNotIn("apt-get upgrade", install)
        self.assertNotIn("apt-get remove", install)
        install_fn = install.split("install_pod() {", 1)[1].split("\ndrop_id_lines() {", 1)[0]
        self.assertNotIn("ufw", install_fn)

    def test_copy_targets_only_the_raptorpath_home(self):
        script = COPY.read_text(encoding="utf-8")
        self.assertIn("dest=/home/raptorpath/incoming", script)
        self.assertIn("SSH_USER must be raptorpath", script)
        self.assertIn("bash ${dest}/raptorpath.sh install", script)
        self.assertIn('"$stage/public" "$stage/app"', script)
        self.assertIn("stage_release", script)
        self.assertNotIn("TUNNEL_ENV", script)
        self.assertNotIn("tunnel.env", script)
        self.assertNotIn("raptorpath-tunnel.container", script)
        self.assertNotIn("raptorpath-cloudflared", script)
        self.assertNotIn("form.env", script)
        self.assertNotIn("FORM_ENV", script)
        self.assertIn(
            "https://raw.githubusercontent.com/Ricodey/engineering-consulting/main/deploy/raptorpath.sh",
            script,
        )
        self.assertNotIn("pancake", script)
        self.assertNotIn("accept-new", script)
        self.assertNotIn("DEPLOY_AUTHORIZED_KEY_FILE", script)
        example = (ROOT / "deploy" / "copy.defaults.example").read_text(encoding="utf-8")
        self.assertIn("SSH_USER=raptorpath", example)
        self.assertNotIn("TUNNEL_ENV", example)
        self.assertNotIn("FORM_ENV", example)
        self.assertNotIn("form.env", example)
        self.assertIn("HOST=\n", example)
        self.assertNotIn("118.127.40.148", example)
        self.assertNotIn("/home/robot", example)
        with tempfile.TemporaryDirectory() as tmp:
            defaults = Path(tmp) / "copy.defaults"
            defaults.write_text(
                "HOST=198.51.100.10\nSSH_PORT=4041\nSSH_USER=raptorpath\nSSH_KEY=/tmp/key\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["COPY_DEFAULTS"] = str(defaults)
            result = run(COPY, "--print-plan", env=env)
            parsed = values_of(result)
            self.assertEqual(parsed["ssh_user"], "raptorpath")
            self.assertEqual(parsed["dest"], "/home/raptorpath/incoming")
            self.assertEqual(parsed["host"], "198.51.100.10")
            self.assertEqual(parsed["port"], "4041")

    def test_bootstrap_checks_the_authorized_key_before_root(self):
        if os.geteuid() == 0:
            self.skipTest("bootstrap would create the raptorpath user as root")
        missing = os.environ.copy()
        missing.pop("DEPLOY_AUTHORIZED_KEY", None)
        result = run(SETUP, "bootstrap", env=missing)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("set DEPLOY_AUTHORIZED_KEY", result.stderr)
        self.assertNotIn("useradd", result.stdout)

        rejected = os.environ.copy()
        rejected["DEPLOY_AUTHORIZED_KEY"] = "not-a-key"
        result = run(SETUP, "bootstrap", env=rejected)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not an OpenSSH public key", result.stderr)

        duplicate = os.environ.copy()
        duplicate["DEPLOY_AUTHORIZED_KEY"] = (
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyOnlyForTheParser one\n"
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyOnlyForTheParser two\n"
        )
        result = run(SETUP, "bootstrap", env=duplicate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must contain one public key", result.stderr)

        accepted = os.environ.copy()
        accepted["DEPLOY_AUTHORIZED_KEY"] = (
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyOnlyForTheParser raptorpath-deploy"
        )
        result = run(SETUP, "bootstrap", env=accepted)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("run bootstrap as root", result.stderr)
        self.assertIn("user=raptorpath", result.stdout)
        self.assertNotIn("useradd", result.stdout)
        self.assertNotIn("ufw", result.stdout)

    def test_failed_cutover_restores_the_release_and_nginx_conf(self):
        deploy = (ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")
        flow = deploy.split('echo "preparing the tunnel"', 1)[1]
        self.assertLess(
            flow.index("nginx.conf.previous"),
            flow.index('"$ROOT/deploy/pod/nginx.conf"'),
        )
        failed = deploy.split('echo "cutting over"', 1)[1]
        self.assertLess(failed.index("cloudflare_tunnel.py"), failed.index("cutover-failed"))
        self.assertLess(failed.index("cutover-failed"), failed.index('echo "live"'))

        def function_text(name):
            start = deploy.index("%s() {" % name)
            index = deploy.index("{", start)
            depth = 0
            while index < len(deploy):
                if deploy.startswith("${", index):
                    index = deploy.index("}", index) + 1
                    continue
                char = deploy[index]
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        return deploy[start : index + 1]
                index += 1
            self.fail("missing %s" % name)

        names = (
            "link_to",
            "drop_old_tunnel",
            "drop_cloudflared_container",
            "drop_brief_container",
            "restart_units",
            "restore_failed_cutover",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "home"
            for stamp, marker in (("old", "previous page"), ("new", "broken page")):
                public = base / "releases" / stamp / "public"
                app = base / "releases" / stamp / "app"
                public.mkdir(parents=True)
                app.mkdir()
                (public / "index.html").write_text(marker + "\n", encoding="utf-8")
                (app / "brief.py").write_text("print('brief')\n", encoding="utf-8")
            (base / "current").symlink_to("releases/new")
            (base / "previous").symlink_to("releases/old")
            (base / "nginx.conf").write_text("broken config\n", encoding="utf-8")
            (base / "nginx.conf.previous").write_text(
                "listen 8080 default_server;\n",
                encoding="utf-8",
            )
            bindir = root / "bin"
            bindir.mkdir()
            log = root / "systemctl.log"
            (bindir / "systemctl").write_text(
                '#!/bin/sh\nprintf \'%s\\n\' "$*" >>"$SYSTEMCTL_LOG"\nexit 0\n',
                encoding="utf-8",
            )
            (bindir / "podman").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            for name in ("systemctl", "podman"):
                (bindir / name).chmod(0o755)
            script = "\n".join(function_text(name) for name in names)
            script += '\nbase="$1"\nrestore_failed_cutover\n'
            result = subprocess.run(
                ["bash", "-c", script, "restore", str(base)],
                cwd=ROOT,
                env={
                    "PATH": "%s:%s" % (bindir, os.environ.get("PATH", "")),
                    "SYSTEMCTL_LOG": str(log),
                    "HOME": str(root),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("restored nginx.conf", result.stdout)
            self.assertIn("restored the previous release", result.stdout)
            self.assertEqual(
                (base / "nginx.conf").read_text(encoding="utf-8"),
                "listen 8080 default_server;\n",
            )
            self.assertFalse((base / "nginx.conf.previous").exists())
            self.assertEqual(os.readlink(base / "current"), "releases/old")
            self.assertEqual(os.readlink(base / "previous"), "releases/new")
            recorded = log.read_text(encoding="utf-8")
            self.assertIn(
                "start raptorpath-pod.service raptorpath-web.service "
                "raptorpath-brief.service raptorpath-cloudflared.service",
                recorded,
            )

    def test_install_waits_for_the_podman_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            incoming = home / "incoming"
            bindir = root / "bin"
            secrets = root / "secrets"
            log = root / "systemctl.log"
            bindir.mkdir()
            secrets.mkdir()
            (bindir / "id").write_text(
                "#!/bin/sh\n"
                'if [ "$1" = "-un" ]; then echo raptorpath; exit 0; fi\n'
                'if [ "$1" = "-u" ]; then echo 1000; exit 0; fi\n'
                'echo "unexpected id $*" >&2; exit 1\n',
                encoding="utf-8",
            )
            (bindir / "podman").write_text(
                "#!/bin/sh\n"
                'if [ "$1" = "--version" ]; then echo "podman version 4.9.3"; exit 0; fi\n'
                'if [ "$1" = "secret" ] && [ "$2" = "inspect" ]; then\n'
                '  if [ -f "$SECRET_DIR/$3" ]; then exit 0; fi\n'
                "  exit 1\n"
                "fi\n"
                'echo "unexpected podman $*" >&2; exit 1\n',
                encoding="utf-8",
            )
            (bindir / "systemctl").write_text(
                '#!/bin/sh\nprintf \'%s\\n\' "$*" >>"$SYSTEMCTL_LOG"\nexit 0\n',
                encoding="utf-8",
            )
            for name in ("id", "podman", "systemctl"):
                (bindir / name).chmod(0o755)

            def lay_incoming():
                pod = incoming / "pod"
                (incoming / "public").mkdir(parents=True)
                (incoming / "app").mkdir()
                pod.mkdir()
                (pod / "raptorpath.pod").write_text("[Pod]\nPodName=raptorpath\n", encoding="utf-8")
                (pod / "nginx.conf").write_text("server { listen 8080; }\n", encoding="utf-8")
                (pod / "raptorpath-web.container").write_text(
                    "[Container]\nContainerName=raptorpath-web\n",
                    encoding="utf-8",
                )
                (pod / "raptorpath-brief.container").write_text(
                    "[Container]\nSecret=raptorpath-email,type=env,target=CF_EMAIL_TOKEN\n",
                    encoding="utf-8",
                )
                (incoming / "public" / "index.html").write_text("<html></html>\n", encoding="utf-8")
                (incoming / "app" / "brief.py").write_text("print('brief')\n", encoding="utf-8")

            env = os.environ.copy()
            env["PATH"] = "%s:%s" % (bindir, env.get("PATH", ""))
            env["RAPTORPATH_HOME"] = str(home)
            env["SECRET_DIR"] = str(secrets)
            env["SYSTEMCTL_LOG"] = str(log)
            lay_incoming()
            first = run(SETUP, "install", env=env)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("the first release will start the units", first.stdout)
            self.assertNotIn("installed the raptorpath pod", first.stdout)
            self.assertTrue((home / "releases" / "initial" / "app" / "brief.py").is_file())
            self.assertTrue((home / "current" / "public" / "index.html").is_file())
            self.assertTrue(
                (
                    home / ".config" / "containers" / "systemd" / "raptorpath-brief.container"
                ).is_file()
            )
            self.assertFalse(incoming.exists())
            recorded = log.read_text(encoding="utf-8")
            self.assertIn("--user daemon-reload", recorded)
            self.assertNotIn("restart", recorded)
            self.assertNotIn("raptorpath-brief.service", recorded)
            self.assertNotIn("raptorpath-cloudflared.service", recorded)

            lay_incoming()
            (secrets / "raptorpath-email").write_text("", encoding="utf-8")
            (secrets / "raptorpath-cloudflared").write_text("", encoding="utf-8")
            log.write_text("", encoding="utf-8")
            second = run(SETUP, "install", env=env)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("installed the raptorpath pod", second.stdout)
            self.assertNotIn("the first release will start the units", second.stdout)
            recorded = log.read_text(encoding="utf-8")
            self.assertIn("--user daemon-reload", recorded)
            self.assertIn(
                "restart raptorpath-pod.service raptorpath-web.service raptorpath-brief.service",
                recorded,
            )
            self.assertNotIn("raptorpath-cloudflared.service", recorded)

    def test_second_bootstrap_leaves_an_existing_install(self):
        script = SETUP.read_text(encoding="utf-8")
        deploy = (ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        copy = COPY.read_text(encoding="utf-8")
        self.assertNotIn("ufw", script)
        self.assertNotIn("visudo", script)
        self.assertNotIn("sudo", script)
        self.assertNotIn("sudo", deploy)
        self.assertNotIn("ufw", workflow)
        self.assertNotIn("ufw", copy)
        self.assertNotIn("sudo", workflow)
        self.assertIn("./deploy/deploy.sh", workflow)
        self.assertIn('DEPLOY_USER" = "raptorpath"', deploy)
        for doc in (
            (ROOT / "AGENTS.md").read_text(encoding="utf-8"),
            (ROOT / "README.md").read_text(encoding="utf-8"),
        ):
            self.assertIn("does not change sshd", doc)
            self.assertIn("Email Sending API over HTTPS", doc)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "marker"
            marker.write_text("installed\n", encoding="utf-8")
            bindir = root / "bin"
            bindir.mkdir()
            (bindir / "id").write_text(
                "#!/bin/sh\n"
                'if [ "$1" = "-u" ] && [ "$#" -eq 1 ]; then echo 0; exit 0; fi\n'
                'if [ "$1" = "-u" ] && [ "$2" = "raptorpath" ]; then echo 1000; exit 0; fi\n'
                'echo "unexpected id $*" >&2; exit 1\n',
                encoding="utf-8",
            )
            (bindir / "podman").write_text(
                "#!/bin/sh\n"
                'if [ "$1" = "--version" ]; then echo "podman version 4.9.3"; exit 0; fi\n'
                'echo "unexpected podman $*" >&2; exit 1\n',
                encoding="utf-8",
            )
            for name in ("id", "podman"):
                (bindir / name).chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = "%s:%s" % (bindir, env.get("PATH", ""))
            env["RAPTORPATH_MARKER"] = str(marker)
            env["RAPTORPATH_SUBUID_FILE"] = str(root / "subuid")
            env["RAPTORPATH_SUBGID_FILE"] = str(root / "subgid")
            env["DEPLOY_AUTHORIZED_KEY"] = (
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyOnlyForTheParser raptorpath-deploy"
            )
            (root / "subuid").write_text("", encoding="utf-8")
            (root / "subgid").write_text("", encoding="utf-8")

            first = run(SETUP, "bootstrap", env=env)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("raptorpath already installed", first.stdout)
            self.assertNotIn("useradd", first.stdout)

            second = run(SETUP, "bootstrap", env=env)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("raptorpath already installed", second.stdout)
            self.assertNotIn("useradd", second.stdout)

            marker.unlink()
            refused = run(SETUP, "bootstrap", env=env)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("not this install", refused.stderr)

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
            self.assertIn("SSH_USER must be raptorpath", result.stderr)

    def test_stage_release_copies_the_page_and_brief(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "stage"
            script = 'set -euo pipefail\n. deploy/stage.sh\nstage_release "$1" "$2"\n'
            result = subprocess.run(
                ["bash", "-c", script, "stage", str(ROOT), str(dest)],
                cwd=ROOT,
                env=os.environ.copy(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((dest / "public" / "index.html").is_file())
            self.assertTrue((dest / "public" / "css" / "styles.css").is_file())
            self.assertTrue((dest / "public" / "js" / "main.js").is_file())
            brief = (dest / "app" / "brief.py").read_text(encoding="utf-8")
            self.assertIn("def mail_settings", brief)
            self.assertEqual(list(dest.glob("app/test_*.py")), [])


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
            urljoin("https://raptorpath.com", "api/brief"),
            "https://raptorpath.com/api/brief",
        )
        self.assertEqual(
            urljoin("https://raptorpath.com", "css/styles.css"),
            "https://raptorpath.com/css/styles.css",
        )

    def test_readme_documents_the_test_hostname(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for doc in (readme, agents):
            self.assertIn("raptorpath.com", doc)
            self.assertNotIn("pancakeboard.com", doc)
            self.assertIn("publishes no host port", doc)
            self.assertIn("raptorpath.sh bootstrap", doc)
            self.assertIn(
                "https://raw.githubusercontent.com/Ricodey/engineering-consulting/main/deploy/raptorpath.sh",
                doc,
            )
            self.assertIn("DEPLOY_AUTHORIZED_KEY=", doc)
            self.assertNotIn("DEPLOY_AUTHORIZED_KEY_FILE", doc)
            self.assertNotIn("ssh -p 4041", doc)
            self.assertNotIn("setup-server.sh", doc)
            self.assertNotIn("setup-runner.sh", doc)
            self.assertNotIn("tunnel that is already running", doc)
            self.assertIn("/home/raptorpath/tunnel.env", doc)
            self.assertIn("docker.io/cloudflare/cloudflared:2026.10.4", doc)
            self.assertIn("http://127.0.0.1:8080", doc)
            self.assertIn("TUNNEL_TOKEN", doc)
            self.assertIn("CLOUDFLARE_API_TOKEN", doc)
            self.assertIn("CLOUDFLARE_ACCOUNT_ID", doc)
            self.assertIn("CLOUDFLARE_ZONE_ID", doc)
            self.assertIn("Account Cloudflare Tunnel Edit", doc)
            self.assertIn("Zone DNS Edit", doc)
            self.assertNotIn("TUNNEL_ENV", doc)
            self.assertNotIn("Create a new tunnel", doc)
            self.assertNotIn("Put the token", doc)
        self.assertIn("/home/raptorpath", readme)
        self.assertIn("/home/raptorpath/incoming/", readme)
        self.assertIn("/home/raptorpath/releases/<timestamp>/", readme)
        self.assertIn("`podman` and `uidmap`", readme)
        self.assertIn("docker.io/library/nginx:1.28-alpine", readme)
        self.assertIn("docker.io/library/python:3.12-slim", readme)
        self.assertNotIn("does not run ufw", agents)
        self.assertNotIn("does not change sshd or the firewall", agents)
        self.assertIn("`podman` and `uidmap`", agents)
