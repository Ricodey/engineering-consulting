import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "deploy" / "setup-server.sh"
ORIGIN_RULE = (
    '(http.host eq "pancakeboard.com" and '
    '(http.request.uri.path eq "/consulting" or '
    'starts_with(http.request.uri.path, "/consulting/")))'
)


def run_setup(*args, env):
    return subprocess.run(
        ["bash", str(SETUP), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def base_env():
    env = os.environ.copy()
    env.pop("SITE_PREFIX", None)
    env.pop("SITE_DOMAIN", None)
    return env


def render(prefix, domain):
    env = base_env()
    env["SITE_PREFIX"] = prefix
    env["SITE_DOMAIN"] = domain
    result = run_setup("--print-nginx", env=env)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout


def plan(prefix=None, domain=None):
    env = base_env()
    if prefix is not None:
        env["SITE_PREFIX"] = prefix
    if domain is not None:
        env["SITE_DOMAIN"] = domain
    result = run_setup("--print-plan", env=env)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    values = {}
    for line in result.stdout.splitlines():
        key, value = line.split("=", 1)
        values[key] = value
    return values


def function_body(text, name):
    marker = name + "() {"
    start = text.index(marker)
    brace = text.index("{", start)
    depth = 0
    for index, char in enumerate(text[brace:], brace):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError("unbalanced " + name)


class PrefixPlanTests(unittest.TestCase):
    def test_default_is_the_dedicated_host(self):
        values = plan()
        self.assertEqual(values["host_mode"], "dedicated")
        self.assertEqual(values["listen_port"], "443")
        self.assertEqual(values["site_prefix"], "/")
        self.assertEqual(values["configure_ssh"], "yes")
        self.assertEqual(values["configure_firewall"], "yes")

    def test_root_prefix_is_dedicated(self):
        values = plan("/", "datum.studio")
        self.assertEqual(values["host_mode"], "dedicated")
        self.assertEqual(values["listen_port"], "443")

    def test_shared_prefix_skips_ssh_and_firewall(self):
        values = plan("/consulting", "pancakeboard.com")
        self.assertEqual(values["host_mode"], "shared")
        self.assertEqual(values["listen_port"], "8443")
        self.assertEqual(values["site_prefix"], "/consulting")
        self.assertEqual(values["configure_ssh"], "no")
        self.assertEqual(values["configure_firewall"], "no")

    def test_another_prefix_uses_the_same_shared_port(self):
        values = plan("/studio", "studio.example")
        self.assertEqual(values["host_mode"], "shared")
        self.assertEqual(values["listen_port"], "8443")
        self.assertEqual(values["site_prefix"], "/studio")

    def test_bad_prefixes_are_rejected(self):
        for prefix in ("consulting", "/consulting/", "/a/b", "/../x", "/consulting?x", "//x"):
            result = run_setup("--print-plan", env={**base_env(), "SITE_PREFIX": prefix})
            self.assertNotEqual(result.returncode, 0, prefix)
            self.assertIn("SITE_PREFIX", result.stderr)

    def test_ssh_and_firewall_commands_stay_in_the_dedicated_function(self):
        script = SETUP.read_text(encoding="utf-8")
        body = function_body(script, "configure_dedicated_host")
        self.assertIn("ufw allow 443/tcp", body)
        self.assertIn("ufw allow 4041/tcp", body)
        self.assertIn("Port 4041", body)
        self.assertIn("systemctl reload ssh", body)
        outside = script.replace(body, "", 1)
        self.assertNotIn("ufw allow", outside)
        self.assertNotIn("ufw default", outside)
        self.assertNotIn("ufw --force", outside)
        self.assertNotIn("Port 4041", outside)
        self.assertNotIn("systemctl reload ssh", outside)
        self.assertNotIn("sshd -t", outside)

    def test_code_does_not_hardcode_the_test_domain(self):
        for relative in (
            "deploy/setup-server.sh",
            "deploy/nginx-site.conf",
            "deploy/deploy.sh",
            "server/brief.py",
            "js/main.js",
            "index.html",
        ):
            self.assertNotIn("pancakeboard", (ROOT / relative).read_text(encoding="utf-8"))

    def test_deploy_accepts_the_ssh_port_from_the_environment(self):
        deploy = (ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")
        self.assertNotIn("SSH is on port 4041", deploy)
        self.assertIn("DEPLOY_SSH_PORT must be a TCP port", deploy)


class PrefixNginxTests(unittest.TestCase):
    def test_dedicated_site_listens_on_443(self):
        config = render("/", "datum.studio")
        self.assertIn("listen 443 ssl;", config)
        self.assertIn("listen [::]:443 ssl;", config)
        self.assertIn("server_name datum.studio;", config)
        self.assertIn("location = /api/brief {", config)
        self.assertIn("proxy_pass http://127.0.0.1:4174/api/brief;", config)
        self.assertIn("root /var/www/datum/current/public;", config)
        self.assertIn("try_files $uri $uri/ =404;", config)
        self.assertIn("absolute_redirect off;", config)
        self.assertNotIn("8443", config)
        self.assertNotIn("return 404", config)
        self.assertNotIn("alias ", config)
        self.assertNotIn("pancakeboard", config)
        self.assertNotIn("__LISTEN__", config)
        self.assertNotIn("__LOCATIONS__", config)

    def test_shared_site_listens_only_on_8443(self):
        config = render("/consulting", "pancakeboard.com")
        self.assertIn("listen 8443 ssl;", config)
        self.assertIn("listen [::]:8443 ssl;", config)
        self.assertIn("server_name pancakeboard.com;", config)
        self.assertIn("location = /consulting/api/brief {", config)
        self.assertIn("proxy_pass http://127.0.0.1:4174/api/brief;", config)
        self.assertIn("location = /consulting {", config)
        self.assertIn("return 301 /consulting/;", config)
        self.assertIn("location /consulting/ {", config)
        self.assertIn("alias /var/www/datum/current/public/;", config)
        self.assertIn("return 404;", config)
        self.assertIn("absolute_redirect off;", config)
        self.assertIn("port_in_redirect off;", config)
        self.assertNotIn("try_files", config)
        self.assertNotIn("listen 443", config)
        self.assertNotIn("listen 80", config)
        self.assertNotIn("listen [::]:443", config)
        self.assertNotRegex(config, r"return 301 https?://")
        for line in config.splitlines():
            if "return 301" in line:
                self.assertNotIn("8443", line)

    def test_prefix_is_not_fixed_to_the_test_value(self):
        config = render("/studio", "studio.example")
        self.assertIn("listen 8443 ssl;", config)
        self.assertIn("location = /studio/api/brief {", config)
        self.assertIn("return 301 /studio/;", config)
        self.assertIn("server_name studio.example;", config)
        self.assertNotIn("/consulting", config)

    def test_rendered_config_passes_nginx_t(self):
        nginx = subprocess.run(["nginx", "-v"], capture_output=True, text=True, check=False)
        self.assertEqual(nginx.returncode, 0, nginx.stderr)
        for prefix, domain in (("/", "datum.studio"), ("/consulting", "pancakeboard.com")):
            with self.subTest(prefix=prefix):
                self.assert_nginx_ok(render(prefix, domain))

    def assert_nginx_ok(self, site):
        tmp = tempfile.mkdtemp()
        try:
            root = Path(tmp)
            key = root / "origin.key"
            cert = root / "origin.pem"
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-nodes",
                    "-newkey",
                    "rsa:2048",
                    "-keyout",
                    str(key),
                    "-out",
                    str(cert),
                    "-subj",
                    "/CN=test",
                    "-days",
                    "1",
                ],
                check=True,
                capture_output=True,
            )
            site = site.replace("/etc/ssl/cloudflare/origin.pem", str(cert))
            site = site.replace("/etc/ssl/cloudflare/origin.key", str(key))
            site_path = root / "site.conf"
            site_path.write_text(site, encoding="utf-8")
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
                        "    geo $remote_addr $is_cloudflare {",
                        "        default 0;",
                        "        127.0.0.1 1;",
                        "    }",
                        "    map $http_cf_connecting_ip $brief_ip {",
                        '        ""      $remote_addr;',
                        "        default $http_cf_connecting_ip;",
                        "    }",
                        "    include %s;" % site_path,
                        "}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            # nginx -t binds the listen ports. 443 needs root.
            result = subprocess.run(
                ["sudo", "nginx", "-t", "-c", str(wrapper)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        finally:
            subprocess.run(["sudo", "rm", "-rf", tmp], check=True)


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
            urljoin("https://shared.example/consulting/", "api/brief"),
            "https://shared.example/consulting/api/brief",
        )
        self.assertEqual(
            urljoin("https://shared.example/consulting/", "css/styles.css"),
            "https://shared.example/consulting/css/styles.css",
        )
        self.assertEqual(
            urljoin(
                "https://shared.example/consulting/css/styles.css",
                "../assets/fonts/inter-tight.woff2",
            ),
            "https://shared.example/consulting/assets/fonts/inter-tight.woff2",
        )
        self.assertEqual(
            urljoin("https://dedicated.example/", "api/brief"),
            "https://dedicated.example/api/brief",
        )
        self.assertEqual(
            urljoin("https://shared.example/consulting/", "#work"),
            "https://shared.example/consulting/#work",
        )

    def test_readme_documents_the_origin_rule(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(ORIGIN_RULE, readme)
        self.assertIn("SITE_PREFIX=/consulting", readme)
        self.assertIn("SITE_DOMAIN=pancakeboard.com", readme)
        self.assertIn("8443", readme)
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("SITE_PREFIX=/consulting", agents)
        self.assertIn("8443", agents)
        self.assertIn("does not change sshd or the firewall", agents)
