import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import brief

ROOT = Path(__file__).resolve().parents[1]
# Not a mailbox password. These tests never open a socket to smtp.gmail.com.
FIXTURE = "fixture"


FULL_SERVICES = (
    "Product Design Concepts",
    "Circuit Board Design",
    "Mechanical Design",
    "Prototyping",
    "Complete Product Solutions",
)


def valid_payload(**overrides):
    payload = {
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "company": "Analytical Engines",
        "services": ["Product Design Concepts", "Circuit Board Design"],
        "task": "A bench prototype that has to boot and charge.",
        "fax": "",
    }
    payload.update(overrides)
    return payload


class MailConfigTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("SMTP_PASSWORD")
        os.environ["SMTP_PASSWORD"] = FIXTURE

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("SMTP_PASSWORD", None)
        else:
            os.environ["SMTP_PASSWORD"] = self._saved

    def test_starttls_login_and_send_on_port_587(self):
        calls = []

        class FakeSMTP:
            def __init__(self, host, port, timeout=None):
                calls.append(("connect", host, port, timeout))

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def ehlo(self):
                calls.append("ehlo")

            def starttls(self, context=None):
                calls.append(("starttls", context is not None))

            def login(self, user, password):
                calls.append(("login", user, password))

            def sendmail(self, sender, recipients, payload):
                calls.append(("send", sender, recipients, payload))

        original = brief.smtplib.SMTP
        brief.smtplib.SMTP = FakeSMTP
        try:
            message = brief.compose(
                {
                    "name": "Ada Lovelace",
                    "email": "ada@example.com",
                    "company": "Analytical Engines",
                    "services": list(FULL_SERVICES),
                    "task": "A bench prototype that has to boot and charge.",
                },
                brief.mail_settings(),
            )
            brief.send_mail(message)
        finally:
            brief.smtplib.SMTP = original

        self.assertEqual(
            calls,
            [
                ("connect", "smtp.gmail.com", 587, 20),
                "ehlo",
                ("starttls", True),
                "ehlo",
                ("login", "fig@mail.grokbot.com", FIXTURE),
                (
                    "send",
                    "fig@mail.grokbot.com",
                    ["fig@mail.grokbot.com"],
                    brief.wire_bytes(message),
                ),
            ],
        )

    def test_from_lines_in_the_brief_are_not_escaped(self):
        message = brief.compose(
            {
                "name": "Ada Lovelace",
                "email": "ada@example.com",
                "company": "Analytical Engines",
                "services": [],
                "task": "From the bench.\nFrom a concept.",
            },
            brief.mail_settings(),
        )
        raw = brief.wire_bytes(message).decode()
        self.assertIn("\r\nFrom the bench.\r\n", raw)
        self.assertIn("\r\nFrom a concept.\r\n", raw)
        self.assertNotIn(">From", raw)

    def test_missing_password_is_not_configured(self):
        os.environ["SMTP_PASSWORD"] = ""
        self.assertIsNone(brief.mail_settings())
        with self.assertRaises(brief.MailNotConfigured):
            brief.send_mail(brief.EmailMessage())

    def test_mailbox_is_one_address(self):
        self.assertEqual(brief.SMTP_HOST, "smtp.gmail.com")
        self.assertEqual(brief.SMTP_USER, "fig@mail.grokbot.com")
        self.assertEqual(brief.SMTP_FROM, brief.SMTP_USER)
        self.assertEqual(brief.MAIL_TO, brief.SMTP_USER)
        self.assertNotIn(",", brief.MAIL_TO)
        settings = brief.mail_settings()
        self.assertEqual(settings["MAIL_TO"], "fig@mail.grokbot.com")
        self.assertEqual(settings["SMTP_USER"], "fig@mail.grokbot.com")
        self.assertNotIn("\n", settings["SMTP_PASSWORD"])

    def test_newline_in_the_password_is_not_configured(self):
        os.environ["SMTP_PASSWORD"] = "abc\n"
        self.assertIsNone(brief.mail_settings())


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("SMTP_PASSWORD")
        os.environ["SMTP_PASSWORD"] = FIXTURE
        brief.HITS.clear()
        self.sent = []
        self._send_mail = brief.send_mail
        brief.send_mail = self._capture
        self.httpd = brief.Server(("127.0.0.1", 0), brief.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        brief.send_mail = self._send_mail
        brief.HITS.clear()
        if self._saved is None:
            os.environ.pop("SMTP_PASSWORD", None)
        else:
            os.environ["SMTP_PASSWORD"] = self._saved

    def _capture(self, message):
        self.sent.append(message)

    def post(self, payload, ip="203.0.113.5", content_type="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        request = urllib.request.Request(
            "http://127.0.0.1:%s/api/brief" % self.port,
            data=data,
            method="POST",
            headers={"Content-Type": content_type, "X-Real-IP": ip},
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            return exc.code, json.loads(raw) if raw else {}

    def test_health(self):
        with urllib.request.urlopen(
            "http://127.0.0.1:%s/health" % self.port, timeout=2
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read().decode()), {"ok": True})

    def test_consultation_request_is_emailed(self):
        status, body = self.post(valid_payload())
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})
        self.assertEqual(len(self.sent), 1)
        message = self.sent[0]
        self.assertEqual(message["To"], "fig@mail.grokbot.com")
        self.assertEqual(message["From"], "fig@mail.grokbot.com")
        self.assertEqual(message["Reply-To"], "ada@example.com")
        self.assertEqual(message["Subject"], "Consultation request")
        text = message.get_content()
        self.assertIn("Datum \u2014 consultation request", text)
        self.assertIn("Name: Ada Lovelace", text)
        self.assertIn("Email: ada@example.com", text)
        self.assertIn("Company: Analytical Engines", text)
        self.assertIn("Services required:\nProduct Design Concepts\nCircuit Board Design\n", text)
        self.assertIn(
            "Description of the task\nA bench prototype that has to boot and charge.", text
        )
        self.assertNotIn("brief", text.lower())
        self.assertNotIn("fax", text.lower())

    def test_full_service_list_is_in_the_email(self):
        status, _body = self.post(valid_payload(services=list(reversed(FULL_SERVICES))))
        self.assertEqual(status, 200)
        text = self.sent[0].get_content()
        self.assertIn("Services required:\n" + "\n".join(FULL_SERVICES) + "\n", text)
        self.assertNotIn("brief", text.lower())

    def test_empty_services_use_the_em_dash(self):
        status, _body = self.post(valid_payload(services=[]))
        self.assertEqual(status, 200)
        self.assertIn("Services required:\n\u2014\n", self.sent[0].get_content())

    def test_omitted_services_are_accepted(self):
        payload = valid_payload()
        del payload["services"]
        status, _body = self.post(payload)
        self.assertEqual(status, 200)
        self.assertIn("Services required:\n\u2014\n", self.sent[0].get_content())

    def test_missing_company_is_rejected(self):
        status, body = self.post(valid_payload(company="   "))
        self.assertEqual(status, 422)
        self.assertEqual(body["fields"]["company"], "Add your company.")
        self.assertEqual(self.sent, [])

    def test_crlf_in_the_task_is_normalized(self):
        status, _body = self.post(valid_payload(task="line\r\nnext"))
        self.assertEqual(status, 200)
        text = self.sent[0].get_content()
        self.assertIn("line\nnext", text)
        self.assertNotIn("\r", text)

    def test_honeypot_is_dropped(self):
        status, body = self.post(valid_payload(fax="https://spam.example"))
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})
        self.assertEqual(self.sent, [])

    def test_honeypot_fills_the_rate_limit_without_admitting_it(self):
        for _ in range(brief.LIMIT):
            status, body = self.post(valid_payload(fax="bot"))
            self.assertEqual((status, body), (200, {"ok": True}))
        status, body = self.post(valid_payload())
        self.assertEqual(status, 429)
        self.assertEqual(self.sent, [])
        status, body = self.post(valid_payload(fax="bot"))
        self.assertEqual((status, body), (200, {"ok": True}))

    def test_rate_limit_is_per_address(self):
        brief.HITS["203.0.113.5"] = [1000] * brief.LIMIT
        self.assertFalse(brief.allow("203.0.113.5", now=1000))
        self.assertTrue(brief.allow("203.0.113.6", now=1000))

    def test_old_hits_expire(self):
        brief.HITS["203.0.113.8"] = [0] * brief.LIMIT
        self.assertTrue(brief.allow("203.0.113.8", now=brief.WINDOW_SECONDS + 5))

    def test_missing_required_fields_and_bad_email(self):
        status, body = self.post(valid_payload(name="", email="not-an-email", company="", task=""))
        self.assertEqual(status, 422)
        self.assertEqual(body["fields"]["name"], "Add your name.")
        self.assertEqual(body["fields"]["email"], "Add a complete email address.")
        self.assertEqual(body["fields"]["company"], "Add your company.")
        self.assertEqual(body["fields"]["task"], "Describe the task.")
        self.assertNotIn("services", body["fields"])
        self.assertEqual(self.sent, [])

    def test_unknown_service_is_rejected(self):
        for value in ("Design Production", "Firmware", ""):
            self.sent.clear()
            status, body = self.post(valid_payload(services=["Circuit Board Design", value]))
            self.assertEqual(status, 422, value)
            self.assertEqual(body["fields"]["services"], "Choose services from the list.", value)
            self.assertEqual(self.sent, [])

    def test_service_value_must_be_a_list(self):
        status, body = self.post(valid_payload(services="Circuit Board Design"))
        self.assertEqual(status, 422)
        self.assertEqual(body["fields"]["services"], "Choose services from the list.")
        self.assertEqual(self.sent, [])

    def test_invalid_fields(self):
        status, body = self.post(
            valid_payload(
                name="Ada\nBcc: x",
                email="ada@example..com",
                services=["Mechanical Design", "Not a service"],
                task="x" * 4001,
            )
        )
        self.assertEqual(status, 422)
        self.assertEqual(body["fields"]["name"], "Remove unusual characters from this field.")
        self.assertEqual(body["fields"]["email"], "Add a complete email address.")
        self.assertEqual(body["fields"]["services"], "Choose services from the list.")
        self.assertEqual(body["fields"]["task"], "Keep this to 4000 characters.")
        self.assertEqual(self.sent, [])

    def test_unknown_field_is_rejected(self):
        payload = valid_payload()
        payload["website"] = "https://example.com"
        status, body = self.post(payload)
        self.assertEqual(status, 400)
        self.assertEqual(self.sent, [])
        self.assertNotIn("website", json.dumps(body))

    def test_oversized_body(self):
        status, body = self.post(b"{" + b"x" * (brief.MAX_BODY + 1))
        self.assertEqual(status, 413)
        self.assertEqual(self.sent, [])
        self.assertEqual(body["error"], "brief is too large")

    def test_smtp_failure_does_not_leak(self):
        def explode(_message):
            raise OSError("auth failed")

        brief.send_mail = explode
        status, body = self.post(valid_payload())
        self.assertEqual(status, 502)
        encoded = json.dumps(body)
        self.assertNotIn(FIXTURE, encoded)
        self.assertNotIn("password", encoded)
        self.assertEqual(body["error"], "could not send")

    def test_unconfigured_mail(self):
        os.environ["SMTP_PASSWORD"] = ""
        status, body = self.post(valid_payload())
        self.assertEqual(status, 503)
        self.assertEqual(body["error"], "mail is not configured")
        self.assertEqual(self.sent, [])

    def test_plain_text_is_rejected(self):
        status, _body = self.post(b"name=Ada", content_type="text/plain")
        self.assertEqual(status, 415)
        self.assertEqual(self.sent, [])


class PageContractTests(unittest.TestCase):
    def test_form_posts_without_showing_the_honeypot(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "js/main.js").read_text(encoding="utf-8")
        css = (ROOT / "css/styles.css").read_text(encoding="utf-8")
        self.assertIn('name="fax"', html)
        self.assertIn('class="hp"', html)
        self.assertIn('fetch("api/brief"', js)
        self.assertNotIn('fetch("/', js)
        self.assertIn(".hp {", css)
        self.assertEqual(brief.SERVICES, FULL_SERVICES)
        position = 0
        for service in FULL_SERVICES:
            found = html.find('value="%s"' % service, position)
            self.assertGreater(found, position, service)
            position = found
            self.assertIn(service, js)
        self.assertIn("Arrange a consultation", html)
        self.assertIn("Services required", html)
        self.assertIn("Description of the task", html)
        self.assertIn("Consultation requested", html)
        self.assertIn("Copy request", html)
        self.assertIn("Edit the request", html)
        self.assertIn("consultation request", js)
        self.assertNotIn("Prepare the brief", html)
        self.assertNotIn("Copy brief", html)
        self.assertNotIn("Edit the brief", html)
        self.assertNotIn("Brief ready", html)
        self.assertNotIn("project brief", html)
        self.assertNotIn("project brief", js)
        self.assertNotIn("The brief did not send", js)
        self.assertIn("mailto:hello@datum.studio", html)
        self.assertNotIn("Your brief stays in this browser", html)
