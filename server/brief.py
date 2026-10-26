#!/usr/bin/env python3
"""Accept one consultation request and send it by authenticated SMTP.

The process listens on 127.0.0.1:4174. nginx is the only public entry.
Secrets come from the environment. A non-empty fax field is a honeypot:
the request is acknowledged and dropped.
"""

import io
import json
import os
import re
import smtplib
import ssl
import sys
import time
from email.generator import BytesGenerator
from email.message import EmailMessage
from email.utils import getaddresses
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = 4174
MAX_BODY = 32 * 1024
LIMIT = 5
WINDOW_SECONDS = 600
SUBJECT = "Consultation request"
SERVICES = (
    "Design Concepts",
    "PCB Design",
    "Mechanical Design",
    "First of kind Prototype",
    "Design for scaled Production",
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
    settings = {}
    for key in ("SMTP_HOST", "SMTP_USER", "SMTP_FROM", "MAIL_TO"):
        value = os.environ.get(key, "").strip()
        if not value or "\n" in value or "\r" in value or len(value) > 320:
            return None
        settings[key] = value
    password = os.environ.get("SMTP_PASSWORD", "")
    if password == "" or "\n" in password or "\r" in password:
        return None
    if not EMAIL_RE.fullmatch(settings["MAIL_TO"]) or ".." in settings["MAIL_TO"]:
        return None
    settings["SMTP_PASSWORD"] = password
    return settings


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
        "Datum \u2014 consultation request",
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


def compose(fields, settings):
    message = EmailMessage()
    message["Subject"] = SUBJECT
    message["From"] = settings["SMTP_FROM"]
    message["To"] = settings["MAIL_TO"]
    message["Reply-To"] = fields["email"]
    message.set_content(consultation_body(fields), cte="quoted-printable")
    return message


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


def wire_bytes(message):
    # smtplib.send_message escapes lines that start with "From " when the
    # addresses are ASCII. Flatten with the message policy so the message is unchanged.
    buffer = io.BytesIO()
    BytesGenerator(buffer, policy=message.policy).flatten(message, linesep="\r\n")
    return buffer.getvalue()


def send_mail(message):
    settings = mail_settings()
    if settings is None:
        raise MailNotConfigured()
    sender = getaddresses([settings["SMTP_FROM"]])[0][1]
    with smtplib.SMTP(settings["SMTP_HOST"], 587, timeout=20) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(settings["SMTP_USER"], settings["SMTP_PASSWORD"])
        smtp.sendmail(sender, [settings["MAIL_TO"]], wire_bytes(message))


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
        message = compose(payload, settings)
        try:
            send_mail(message)
        except MailNotConfigured:
            self._send(503, {"error": "mail is not configured"})
        except Exception as exc:
            print(
                "brief: smtp failed: " + type(exc).__name__,
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
