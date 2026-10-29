#!/usr/bin/env python3
"""Accept one consultation request and send it through Cloudflare Email Service.

The process listens on 127.0.0.1:4174. nginx is the only public entry.
Mail is a POST to the Email Sending REST API, from
enquiries@pancakeboard.com to fig@mail.grokbot.com. Reply-To is the
submitter. CF_EMAIL_TOKEN comes from the environment. A non-empty fax
field is a honeypot: the request is acknowledged and dropped.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = 4174
CF_ACCOUNT_ID = "6dbcc576b9217229725b3b24009e9497"
EMAIL_URL = "https://api.cloudflare.com/client/v4/accounts/" + CF_ACCOUNT_ID + "/email/sending/send"
MAIL_FROM = "enquiries@pancakeboard.com"
MAIL_TO = "fig@mail.grokbot.com"
MAX_BODY = 32 * 1024
LIMIT = 5
WINDOW_SECONDS = 600
SUBJECT = "Consultation request"
SERVICES = (
    "Product Design Concepts",
    "Circuit Board Design",
    "Mechanical Design",
    "Prototyping",
    "Complete Product Solutions",
)
FIELDS = {"name", "email", "company", "services", "task", "fax"}
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
IP_RE = re.compile(r"^[0-9A-Fa-f:.]+$")
UNUSUAL = "Remove unusual characters from this field."

HITS = {}
LOCK = __import__("threading").Lock()


class MailNotConfigured(RuntimeError):
    pass


class Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def mail_settings():
    token = os.environ.get("CF_EMAIL_TOKEN", "")
    if token == "" or "\n" in token or "\r" in token:
        return None
    if not EMAIL_RE.fullmatch(MAIL_TO) or ".." in MAIL_TO:
        return None
    if not EMAIL_RE.fullmatch(MAIL_FROM) or ".." in MAIL_FROM:
        return None
    return {"CF_EMAIL_TOKEN": token}


def single_line(value, limit, empty_message, length_message):
    if not isinstance(value, str):
        return None, UNUSUAL
    value = value.strip()
    if empty_message and not value:
        return None, empty_message
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return None, UNUSUAL
    if len(value) > limit:
        return None, length_message
    return value, ""


def multi_line(value, empty_message):
    if not isinstance(value, str):
        return None, UNUSUAL
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value:
        return None, empty_message
    if any((ord(ch) < 32 and ch not in "\n\t") or ord(ch) == 127 for ch in value):
        return None, UNUSUAL
    if len(value) > 4000:
        return None, "Keep this to 4000 characters."
    return value, ""


def email_address(value):
    if not isinstance(value, str):
        return None, "Add a complete email address."
    value = value.strip()
    if not value or len(value) > 254 or ".." in value or EMAIL_RE.fullmatch(value) is None:
        return None, "Add a complete email address."
    return value, ""


def services_list(value):
    if value == []:
        return [], ""
    if not isinstance(value, list):
        return None, "Choose services from the list."
    chosen = []
    for item in value:
        if not isinstance(item, str) or item not in SERVICES:
            return None, "Choose services from the list."
        if item not in chosen:
            chosen.append(item)
    return [item for item in SERVICES if item in chosen], ""


def validate(data):
    """Return ("ok", fields), ("drop", None), ("bad", None), or ("invalid", errors)."""
    if not isinstance(data, dict) or not set(data).issubset(FIELDS):
        return "bad", None
    fax = data.get("fax", "")
    if not isinstance(fax, str) or fax.strip():
        return "drop", None

    errors = {}
    name, name_error = single_line(
        data.get("name", ""),
        120,
        "Add your name.",
        "Keep the name to 120 characters.",
    )
    if name_error:
        errors["name"] = name_error
    email, email_error = email_address(data.get("email", ""))
    if email_error:
        errors["email"] = email_error
    company, company_error = single_line(
        data.get("company", ""),
        160,
        "Add your company.",
        "Keep the company to 160 characters.",
    )
    if company_error:
        errors["company"] = company_error
    services, services_error = services_list(data.get("services", []))
    if services_error:
        errors["services"] = services_error
    task, task_error = multi_line(
        data.get("task", ""),
        "Describe the task.",
    )
    if task_error:
        errors["task"] = task_error
    if errors:
        return "invalid", errors
    return "ok", {
        "name": name,
        "email": email,
        "company": company,
        "services": services,
        "task": task,
    }


def consultation_body(fields):
    services = fields["services"]
    service_lines = "\n".join(services) if services else "\u2014"
    lines = [
        "Raptor Path \u2014 consultation request",
        "",
        "Name: " + fields["name"],
        "Email: " + fields["email"],
        "Company: " + fields["company"],
        "Services required:",
        service_lines,
        "",
        "Description of the task",
        fields["task"],
    ]
    return "\n".join(lines) + "\n"


def email_payload(fields):
    return {
        "from": MAIL_FROM,
        "to": [MAIL_TO],
        "reply_to": fields["email"],
        "subject": SUBJECT,
        "text": consultation_body(fields),
    }


def allow(ip, now=None):
    now = time.monotonic() if now is None else now
    with LOCK:
        stale = [
            key for key, bucket in HITS.items() if not bucket or bucket[-1] < now - WINDOW_SECONDS
        ]
        for key in stale:
            del HITS[key]
        bucket = [stamp for stamp in HITS.get(ip, []) if stamp >= now - WINDOW_SECONDS]
        if len(bucket) >= LIMIT:
            HITS[ip] = bucket
            return False
        bucket.append(now)
        HITS[ip] = bucket
        return True


def request_ip(handler):
    raw = (handler.headers.get("X-Real-IP") or "").strip()
    if IP_RE.fullmatch(raw):
        return raw
    return handler.client_address[0]


class MailFailed(RuntimeError):
    pass


def _email_failure_detail(status, raw):
    code = ""
    message = ""
    parsed = None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        parsed = None
    if isinstance(parsed, dict):
        errors = parsed.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            first = errors[0]
            if isinstance(first.get("code"), int):
                code = str(first["code"])
            msg = first.get("message")
            if isinstance(msg, str):
                message = " ".join(msg.split())
    token = os.environ.get("CF_EMAIL_TOKEN", "")
    if token:
        message = message.replace(token, "")
    detail = "HTTP " + str(status)
    if code:
        detail += " code " + code
    if message:
        detail += " " + message
    return detail


def _log_email_failure(status, raw):
    print(
        "brief: email failed: " + _email_failure_detail(status, raw),
        file=sys.stderr,
        flush=True,
    )


def send_mail(payload):
    settings = mail_settings()
    if settings is None:
        raise MailNotConfigured()
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        EMAIL_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + settings["CF_EMAIL_TOKEN"],
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        _log_email_failure(exc.code, exc.read())
        raise MailFailed() from None
    except urllib.error.URLError as exc:
        print(
            "brief: email failed: " + type(exc).__name__,
            file=sys.stderr,
            flush=True,
        )
        raise MailFailed() from None
    if status < 200 or status >= 300:
        _log_email_failure(status, raw)
        raise MailFailed()
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _log_email_failure(status, b"")
        raise MailFailed() from None
    if not isinstance(parsed, dict) or not parsed.get("success"):
        _log_email_failure(status, raw)
        raise MailFailed()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "datum-brief"

    def do_GET(self):
        if self.path.split("?", 1)[0] != "/health":
            self._send(404, {"error": "not found"})
            return
        self._send(200, {"ok": True})

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/api/brief":
            self._send(404, {"error": "not found"})
            return
        raw_length = self.headers.get("Content-Length")
        if raw_length is None or not raw_length.isdigit():
            self._send(411, {"error": "send a content length"})
            return
        length = int(raw_length)
        if length > MAX_BODY:
            self.close_connection = True
            self._send(413, {"error": "brief is too large"})
            return
        if length == 0:
            self._send(400, {"error": "invalid brief"})
            return
        raw = self.rfile.read(length)
        if len(raw) != length:
            self._send(400, {"error": "invalid brief"})
            return
        media = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if media != "application/json":
            self._send(415, {"error": "send json"})
            return
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send(400, {"error": "invalid brief"})
            return

        kind, payload = validate(data)
        if kind == "bad":
            self._send(400, {"error": "invalid brief"})
            return
        if kind == "drop":
            allow(request_ip(self))
            self._send(200, {"ok": True})
            return
        if kind == "invalid":
            self._send(422, {"error": "invalid brief", "fields": payload})
            return
        if not allow(request_ip(self)):
            self._send(429, {"error": "too many briefs"})
            return

        settings = mail_settings()
        if settings is None:
            self._send(503, {"error": "mail is not configured"})
            return
        try:
            send_mail(email_payload(payload))
        except MailNotConfigured:
            self._send(503, {"error": "mail is not configured"})
        except MailFailed:
            self._send(502, {"error": "could not send"})
        except Exception as exc:
            print(
                "brief: email failed: " + type(exc).__name__,
                file=sys.stderr,
                flush=True,
            )
            self._send(502, {"error": "could not send"})
        else:
            self._send(200, {"ok": True})

    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print("brief: " + (fmt % args), file=sys.stderr, flush=True)


def main():
    server = Server((HOST, PORT), Handler)
    print("brief: listening on %s:%s" % (HOST, PORT), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
