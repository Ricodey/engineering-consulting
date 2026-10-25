#!/usr/bin/env python3
"""Serve the Datum page and the brief handler on 127.0.0.1:4173.

The pod does not run this process. A release copies server/brief.py only.
"""

import sys
from pathlib import Path
from urllib.parse import unquote

import brief

PORT = 4173
ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ("assets", "css", "js")
CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".jpg": "image/jpeg",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
}


def local_file(url_path):
    raw = unquote(url_path.split("?", 1)[0])
    if raw in ("/", "/index.html"):
        candidate = (ROOT / "index.html").resolve()
        return candidate if candidate.is_file() else None
    if not raw.startswith("/") or raw.startswith("//"):
        return None
    parts = Path(raw[1:]).parts
    if not parts or parts[0] not in PUBLIC:
        return None
    if any(part in ("", ".", "..") for part in parts):
        return None
    base = (ROOT / parts[0]).resolve()
    candidate = (ROOT / Path(*parts)).resolve()
    if not candidate.is_relative_to(base) or not candidate.is_file():
        return None
    return candidate


def content_type(path):
    return CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


class PreviewHandler(brief.Handler):
    server_version = "datum-preview"

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/health":
            super().do_GET()
            return
        path = local_file(self.path)
        if path is None:
            self._missing()
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type(path))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _missing(self):
        body = b"not found"
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print("preview: " + (fmt % args), file=sys.stderr, flush=True)


def main():
    server = brief.Server((brief.HOST, PORT), PreviewHandler)
    print("preview: listening on %s:%s" % (brief.HOST, PORT), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
