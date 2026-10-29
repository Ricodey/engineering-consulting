import http.client
import json
import os
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import brief
import preview

ROOT = Path(__file__).resolve().parents[1]
MAIL_KEYS = ("CF_EMAIL_TOKEN",)


def valid_brief():
    return {
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "company": "Analytical Engines",
        "services": [
            "Product Design Concepts",
            "Circuit Board Design",
            "Mechanical Design",
            "Prototyping",
            "Complete Product Solutions",
        ],
        "task": "A bench prototype that has to boot and charge.",
        "fax": "",
    }


def header(headers, name):
    for key, value in headers:
        if key.lower() == name.lower():
            return value
    return None


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved_env = {key: os.environ.get(key) for key in MAIL_KEYS}
        for key in MAIL_KEYS:
            os.environ.pop(key, None)
        brief.HITS.clear()
        cls.httpd = brief.Server(("127.0.0.1", 0), preview.PreviewHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        brief.HITS.clear()
        for key, value in cls.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def setUp(self):
        brief.HITS.clear()

    def get(self, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            body = response.read()
            status = response.status
            headers = response.getheaders()
        finally:
            connection.close()
        return status, headers, body

    def post(self, payload):
        data = json.dumps(payload).encode()
        request = urllib.request.Request(
            "http://127.0.0.1:%s/api/brief" % self.port,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_page_matches_the_checkout(self):
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertEqual(body, (ROOT / "index.html").read_bytes())
        self.assertEqual(header(headers, "Cache-Control"), "no-cache")
        self.assertEqual(header(headers, "Content-Type"), "text/html; charset=utf-8")

    def test_css_js_and_asset_match_the_checkout(self):
        for relative in (
            "css/styles.css",
            "js/main.js",
            "assets/favicon.svg",
        ):
            status, headers, body = self.get("/" + relative)
            self.assertEqual(status, 200, relative)
            self.assertEqual(body, (ROOT / relative).read_bytes(), relative)
            self.assertEqual(header(headers, "Cache-Control"), "no-cache", relative)

    def test_checkout_files_outside_the_page_are_hidden(self):
        for path in (
            "/deploy/form.env",
            "/deploy/deploy.env",
            "/.git/HEAD",
            "/server/brief.py",
            "/server/preview.py",
            "/README.md",
            "/css/",
            "/css/../deploy/form.env",
            "/css/%2e%2e/deploy/form.env",
            "/assets/../../.git/HEAD",
        ):
            status, _headers, body = self.get(path)
            self.assertEqual(status, 404, path)
            self.assertEqual(body, b"not found", path)

    def test_health_is_the_handler(self):
        status, headers, body = self.get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body.decode()), {"ok": True})
        content_type = header(headers, "Content-Type")
        self.assertEqual(content_type, "application/json; charset=utf-8")

    def test_brief_without_mail_returns_json(self):
        status, body = self.post(valid_brief())
        self.assertEqual(status, 503)
        self.assertEqual(body, {"error": "mail is not configured"})

    def test_configured_brief_reaches_the_handler(self):
        os.environ["CF_EMAIL_TOKEN"] = "fixture"
        sent = []
        original = brief.send_mail
        brief.send_mail = sent.append
        try:
            status, body = self.post(valid_brief())
        finally:
            brief.send_mail = original
            os.environ.pop("CF_EMAIL_TOKEN", None)
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["to"], ["fig@mail.grokbot.com"])
        self.assertEqual(sent[0]["from"], "enquiries@raptorpath.com")
        self.assertEqual(sent[0]["reply_to"], "ada@example.com")

    def test_docs_preview_on_this_machine(self):
        for name in ("README.md", "AGENTS.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("python3 server/preview.py", text, name)
            self.assertNotIn("http.server", text, name)

    def test_release_omits_the_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "stage"
            script = 'set -euo pipefail\n. deploy/stage.sh\nstage_release "$1" "$2"\n'
            result = subprocess.run(
                ["bash", "-c", script, "stage", str(ROOT), str(dest)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(dest.rglob("preview.py")), [])
            self.assertTrue((dest / "app" / "brief.py").is_file())
