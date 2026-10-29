import os
import subprocess
import tempfile
import unittest
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
    def test_quadlet_publishes_only_loopback(self):
        pod = (POD / "raptorpath.pod").read_text(encoding="utf-8")
        web = (POD / "raptorpath-web.container").read_text(encoding="utf-8")
        brief = (POD / "raptorpath-brief.container").read_text(encoding="utf-8")
        tunnel = (POD / "raptorpath-cloudflared.container").read_text(encoding="utf-8")
        self.assertFalse((POD / "raptorpath-tunnel.container").exists())
        self.assertIn("PodName=raptorpath", pod)
        self.assertIn("PublishPort=127.0.0.1:8443:8080", pod)
        self.assertEqual(pod.count("PublishPort="), 1)
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
        smtp_sudo = "sudo -n /usr/local/sbin/engineering-consulting-smtp"
        self.assertEqual(deploy.count(smtp_sudo), 1)
        self.assertEqual(deploy.count("sudo"), 1)
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
        self.assertNotIn("raptorpath-cloudflared", install_head)
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

    def test_bootstrap_allows_smtp_egress_once(self):
        script = SETUP.read_text(encoding="utf-8")
        bootstrap = script.split("bootstrap() {", 1)[1].split("\ninstall_pod() {", 1)[0]
        installed = bootstrap.split('echo "raptorpath already installed"', 1)[0]
        self.assertIn("install_smtp_helper", installed)
        self.assertIn("install_smtp_sudoers", installed)
        self.assertIn("allow_smtp_egress", installed)
        self.assertLess(
            installed.index("install_smtp_helper"), installed.index("install_smtp_sudoers")
        )
        self.assertLess(
            installed.index("install_smtp_sudoers"), installed.index("allow_smtp_egress")
        )
        self.assertIn('comment="engineering-consulting smtp"', script)
        self.assertIn('visudo -c -f "$tmp"', script)
        self.assertIn(
            'allow out proto tcp to any port 587 comment "$comment"',
            script,
        )
        sudoers_line = (
            'raptorpath ALL=(root) NOPASSWD: /usr/local/sbin/engineering-consulting-smtp ""'
        )
        self.assertEqual(script.count(sudoers_line), 1)
        self.assertNotIn("*", sudoers_line)
        self.assertNotIn('comment "engineering-consulting smtp"', script)
        sudoers_argv = sudoers_line.split("NOPASSWD: ", 1)[1].split()
        self.assertEqual(
            sudoers_argv,
            ["/usr/local/sbin/engineering-consulting-smtp", '""'],
        )
        self.assertNotIn("465", script)
        self.assertNotIn("sudo -n", script)
        self.assertNotIn("NOPASSWD: ALL", script)
        self.assertNotIn("ALL=(ALL)", script)
        deploy = (ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        copy = COPY.read_text(encoding="utf-8")
        smtp_sudo = "sudo -n /usr/local/sbin/engineering-consulting-smtp"
        self.assertEqual(deploy.count(smtp_sudo), 1)
        self.assertEqual(deploy.count("sudo"), 1)
        self.assertLess(
            deploy.index("test -d ${DEPLOY_PATH}/releases"),
            deploy.index(smtp_sudo),
        )
        self.assertLess(deploy.index(smtp_sudo), deploy.index('cloudflare_tunnel.py" prepare'))
        self.assertNotIn("ufw", workflow)
        self.assertNotIn("ufw", copy)
        self.assertNotIn("sudo", workflow)
        self.assertIn("./deploy/deploy.sh", workflow)
        self.assertIn('DEPLOY_USER" = "raptorpath"', deploy)
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for doc in (agents, readme):
            self.assertIn("engineering-consulting smtp", doc)
            self.assertIn("does not change sshd", doc)
            self.assertNotIn("does not change sshd or the firewall", doc)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "marker"
            marker.write_text("installed\n", encoding="utf-8")
            status = root / "status"
            status.write_text("Status: active\n", encoding="utf-8")
            log = root / "ufw.log"
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
            (bindir / "ufw").write_text(
                "#!/bin/sh\n"
                'printf "%s\\n" "$*" >>"$UFW_LOG"\n'
                'if [ "$1" = "status" ]; then cat "$UFW_STATUS"; exit 0; fi\n'
                'if [ "$1" = "allow" ]; then\n'
                '  printf "%s\\n" "587/tcp ALLOW OUT Anywhere # engineering-consulting smtp" >>"$UFW_STATUS"\n'
                "  exit 0\n"
                "fi\n"
                'echo "unexpected ufw $*" >&2; exit 1\n',
                encoding="utf-8",
            )
            for name in ("id", "podman", "ufw"):
                (bindir / name).chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = "%s:%s" % (bindir, env.get("PATH", ""))
            env["RAPTORPATH_MARKER"] = str(marker)
            env["RAPTORPATH_SUBUID_FILE"] = str(root / "subuid")
            env["RAPTORPATH_SUBGID_FILE"] = str(root / "subgid")
            env["UFW_LOG"] = str(log)
            env["UFW_STATUS"] = str(status)
            env["RAPTORPATH_UFW"] = str(bindir / "ufw")
            env["RAPTORPATH_SUDOERS_FILE"] = str(root / "engineering-consulting-smtp")
            env["RAPTORPATH_SMTP_HELPER"] = str(root / "engineering-consulting-smtp-bin")
            env["DEPLOY_AUTHORIZED_KEY"] = (
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyOnlyForTheParser raptorpath-deploy"
            )
            (root / "subuid").write_text("", encoding="utf-8")
            (root / "subgid").write_text("", encoding="utf-8")

            other = status.read_text(encoding="utf-8")
            status.write_text(
                other + "22/tcp ALLOW OUT Anywhere # engineering-consulting smtp\n",
                encoding="utf-8",
            )
            first = run(SETUP, "bootstrap", env=env)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("installed smtp helper", first.stdout)
            self.assertIn("installed smtp sudoers", first.stdout)
            helper = (root / "engineering-consulting-smtp-bin").read_text(encoding="utf-8")
            self.assertIn(
                "exec /usr/sbin/ufw allow out proto tcp to any port 587 "
                "comment 'engineering-consulting smtp'\n",
                helper,
            )
            self.assertEqual(
                oct((root / "engineering-consulting-smtp-bin").stat().st_mode & 0o777), "0o755"
            )
            self.assertIn("allowed outbound tcp 587", first.stdout)
            self.assertIn("raptorpath already installed", first.stdout)
            self.assertNotIn("useradd", first.stdout)
            sudoers = (root / "engineering-consulting-smtp").read_text(encoding="utf-8")
            self.assertEqual(
                sudoers,
                "# engineering-consulting smtp\n%s\n" % sudoers_line,
            )
            self.assertEqual(sudoers.count("NOPASSWD:"), 1)
            checked = subprocess.run(
                ["visudo", "-c", "-f", str(root / "engineering-consulting-smtp")],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(checked.returncode, 0, checked.stderr)
            logged = log.read_text(encoding="utf-8").splitlines()
            allows = [line for line in logged if line.startswith("allow ")]
            self.assertEqual(
                allows,
                ["allow out proto tcp to any port 587 comment engineering-consulting smtp"],
            )

            second = run(SETUP, "bootstrap", env=env)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("smtp helper already installed", second.stdout)
            self.assertNotIn("installed smtp helper", second.stdout)
            self.assertIn("smtp sudoers already installed", second.stdout)
            self.assertNotIn("installed smtp sudoers", second.stdout)
            self.assertIn("smtp egress already allowed", second.stdout)
            self.assertNotIn("allowed outbound tcp 587", second.stdout)
            self.assertEqual(
                (root / "engineering-consulting-smtp").read_text(encoding="utf-8"),
                sudoers,
            )
            logged = log.read_text(encoding="utf-8").splitlines()
            allows = [line for line in logged if line.startswith("allow ")]
            self.assertEqual(len(allows), 1)

            log.write_text("", encoding="utf-8")
            marker.unlink()
            refused = run(SETUP, "bootstrap", env=env)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("not this install", refused.stderr)
            self.assertNotIn("allow ", log.read_text(encoding="utf-8"))

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
            self.assertNotIn("datum.pancakeboard.com", doc)
            self.assertNotIn("pancakeboard.com", doc)
            self.assertIn("127.0.0.1:8443", doc)
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
        self.assertIn("engineering-consulting smtp", agents)
        self.assertIn(
            "sudo -n /usr/local/sbin/engineering-consulting-smtp",
            agents,
        )
        self.assertNotIn("does not run ufw", agents)
        self.assertNotIn("does not change sshd or the firewall", agents)
        self.assertIn("`podman` and `uidmap`", agents)
