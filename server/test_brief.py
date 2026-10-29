import io
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import brief

ROOT = Path(__file__).resolve().parents[1]
# Not an API token. These tests never call Cloudflare.
FIXTURE = "cf-email-fixture-token"


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
        self._saved = os.environ.get("CF_EMAIL_TOKEN")
        os.environ["CF_EMAIL_TOKEN"] = FIXTURE
        self._urlopen = urllib.request.urlopen

    def tearDown(self):
        urllib.request.urlopen = self._urlopen
        if self._saved is None:
            os.environ.pop("CF_EMAIL_TOKEN", None)
        else:
            os.environ["CF_EMAIL_TOKEN"] = self._saved

    def _payload(self):
        return brief.email_payload(
            {
                "name": "Ada Lovelace",
                "email": "ada@example.com",
                "company": "Analytical Engines",
                "services": list(FULL_SERVICES),
                "task": "A bench prototype that has to boot and charge.",
            }
        )

    def test_successful_send_preserves_reply_to(self):
        seen = []

        class Response:
            status = 200

            def read(self):
                body = {
                    "success": True,
                    "errors": [],
                    "result": {"delivered": [brief.MAIL_TO]},
                }
                return json.dumps(body).encode()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def urlopen(request, timeout=None):
            seen.append((request, timeout))
            return Response()

        urllib.request.urlopen = urlopen
        log = io.StringIO()
        saved = sys.stderr
        try:
            sys.stderr = log
            brief.send_mail(self._payload())
        finally:
            sys.stderr = saved
        request, timeout = seen[0]
        body = json.loads(request.data.decode())
        self.assertEqual(len(seen), 1)
        self.assertEqual(timeout, 20)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, brief.EMAIL_URL)
        self.assertEqual(body["from"], "enquiries@raptorpath.com")
        self.assertEqual(body["to"], ["fig@mail.grokbot.com"])
        self.assertEqual(body["reply_to"], "ada@example.com")
        self.assertEqual(body["subject"], "Consultation request")
        self.assertIn("Name: Ada Lovelace", body["text"])
        self.assertEqual(request.get_header("Authorization"), "Bearer " + FIXTURE)
        text = log.getvalue()
        self.assertNotIn(FIXTURE, text)
        self.assertNotIn("Authorization", text)
        self.assertNotIn("Bearer", text)

    def test_missing_token_does_not_send(self):
        os.environ["CF_EMAIL_TOKEN"] = ""
        called = []
        urllib.request.urlopen = lambda *args, **kwargs: called.append(1)
        self.assertIsNone(brief.mail_settings())
        with self.assertRaises(brief.MailNotConfigured):
            brief.send_mail(self._payload())
        self.assertEqual(called, [])

    def test_newline_in_the_token_does_not_send(self):
        os.environ["CF_EMAIL_TOKEN"] = "abc\n"
        called = []
        urllib.request.urlopen = lambda *args, **kwargs: called.append(1)
        self.assertIsNone(brief.mail_settings())
        with self.assertRaises(brief.MailNotConfigured):
            brief.send_mail(self._payload())
        self.assertEqual(called, [])

    def test_non_2xx_logs_status_without_the_token(self):
        def urlopen(request, timeout=None):
            payload = json.dumps(
                {
                    "success": False,
                    "errors": [{"code": 10000, "message": "Authentication error"}],
                }
            ).encode()
            raise urllib.error.HTTPError(
                request.full_url,
                403,
                "Forbidden",
                None,
                io.BytesIO(payload),
            )

        urllib.request.urlopen = urlopen
        log = io.StringIO()
        saved = sys.stderr
        try:
            sys.stderr = log
            with self.assertRaises(brief.MailFailed):
                brief.send_mail(self._payload())
        finally:
            sys.stderr = saved
        text = log.getvalue()
        self.assertIn("brief: email failed: HTTP 403 code 10000 Authentication error", text)
        self.assertNotIn(FIXTURE, text)
        self.assertNotIn("Authorization", text)
        self.assertNotIn("Bearer", text)

    def test_addresses(self):
        source = Path(brief.__file__).read_text(encoding="utf-8")
        self.assertEqual(brief.MAIL_FROM, "enquiries@raptorpath.com")
        self.assertEqual(brief.MAIL_TO, "fig@mail.grokbot.com")
        self.assertEqual(brief.CF_ACCOUNT_ID, "6dbcc576b9217229725b3b24009e9497")
        self.assertNotIn("smtp.gmail.com", source)
        self.assertNotIn("SMTP_PASSWORD", source)
        self.assertNotIn("smtplib", source)


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("CF_EMAIL_TOKEN")
        os.environ["CF_EMAIL_TOKEN"] = FIXTURE
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
            os.environ.pop("CF_EMAIL_TOKEN", None)
        else:
            os.environ["CF_EMAIL_TOKEN"] = self._saved

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
        self.assertEqual(message["to"], ["fig@mail.grokbot.com"])
        self.assertEqual(message["from"], "enquiries@raptorpath.com")
        self.assertEqual(message["reply_to"], "ada@example.com")
        self.assertEqual(message["subject"], "Consultation request")
        text = message["text"]
        self.assertIn("Raptor Path \u2014 consultation request", text)
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
        text = self.sent[0]["text"]
        self.assertIn("Services required:\n" + "\n".join(FULL_SERVICES) + "\n", text)
        self.assertNotIn("brief", text.lower())

    def test_empty_services_use_the_em_dash(self):
        status, _body = self.post(valid_payload(services=[]))
        self.assertEqual(status, 200)
        self.assertIn("Services required:\n\u2014\n", self.sent[0]["text"])

    def test_omitted_services_are_accepted(self):
        payload = valid_payload()
        del payload["services"]
        status, _body = self.post(payload)
        self.assertEqual(status, 200)
        self.assertIn("Services required:\n\u2014\n", self.sent[0]["text"])

    def test_missing_company_is_rejected(self):
        status, body = self.post(valid_payload(company="   "))
        self.assertEqual(status, 422)
        self.assertEqual(body["fields"]["company"], "Add your company.")
        self.assertEqual(self.sent, [])

    def test_crlf_in_the_task_is_normalized(self):
        status, _body = self.post(valid_payload(task="line\r\nnext"))
        self.assertEqual(status, 200)
        text = self.sent[0]["text"]
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
        os.environ["CF_EMAIL_TOKEN"] = ""
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
        self.assertIn("mailto:enquiries@raptorpath.com", html)
        self.assertIn("https://raptorpath.com", html)
        self.assertNotIn("www.raptorpath.com", html)
        self.assertNotIn("Your brief stays in this browser", html)

    def test_consult_submit_reports_loading_success_and_errors(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "js/main.js").read_text(encoding="utf-8")
        css = (ROOT / "css/styles.css").read_text(encoding="utf-8")
        self.assertIn(">Arrange a consultation</button>", html)
        self.assertIn('id="brief-status"', html)
        self.assertIn('role="status"', html)
        self.assertIn("We have your request.", html)
        self.assertLess(js.index("function setBusy"), js.index('fetch("api/brief"'))
        self.assertLess(js.index("setBusy(true)"), js.index('fetch("api/brief"'))
        self.assertIn("submitButton.disabled = busy", js)
        self.assertIn('busy ? "Sending..."', js)
        self.assertIn("Sending the consultation request.", js)
        self.assertIn("Check the highlighted fields.", js)
        self.assertIn("The request did not reach the server.", js)
        self.assertIn("The server returned ", js)
        self.assertIn("The request did not send.", js)
        self.assertIn("sent.hidden = false", js)
        self.assertIn('form.setAttribute("aria-busy", "true")', js)
        self.assertIn("button.button:disabled", css)
        self.assertLess(css.index(".form-note:not(:empty)"), css.index(".form-note.is-error"))
        self.assertNotIn("The brief did not send", js)
