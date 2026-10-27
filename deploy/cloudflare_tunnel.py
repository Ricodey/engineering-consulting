#!/usr/bin/env python3
"""Create the datum tunnel and cut the hostname over.

prepare ensures the remotely managed tunnel, sets its ingress, and writes
the connector token. finish waits until that tunnel is healthy, points
datum.pancakeboard.com at it, then removes only that hostname from every
other tunnel. The token is never printed.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HOSTNAME = "datum.pancakeboard.com"
ORIGIN = "http://127.0.0.1:8080"
INGRESS = (
    {"hostname": HOSTNAME, "service": ORIGIN},
    {"service": "http_status:404"},
)
API_DEFAULT = "https://api.cloudflare.com/client/v4"
SITE_DEFAULT = "https://datum.pancakeboard.com/"
ACCOUNT_RE = re.compile(r"^[0-9a-fA-F]{32}$")
TUNNEL_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
RECORD_RE = re.compile(r"^[0-9a-fA-F]{32}$")


class ApiError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def fail_message(method, path, status, payload):
    messages = []
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list):
            for err in errors:
                if isinstance(err, dict):
                    message = err.get("message")
                    if isinstance(message, str) and message != "":
                        messages.append(message)
    detail = "; ".join(messages) if messages else "request failed"
    return f"cloudflared: {method} {path} failed ({status}) ({detail})"


def token_ok(token):
    if not isinstance(token, str) or token == "":
        return False
    for char in token:
        if char in "\"'\\" or ord(char) < 32:
            return False
    return True


def norm_host(value):
    if not isinstance(value, str):
        return ""
    return value.strip().rstrip(".").lower()


def is_datum_rule(rule):
    return isinstance(rule, dict) and norm_host(rule.get("hostname")) == HOSTNAME


def require_ident(label, value, pattern):
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ApiError(f"cloudflared: {label} is invalid")
    return value


def load_secrets():
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    if token == "" or any(ord(char) < 32 for char in token):
        raise ApiError("cloudflared: set CLOUDFLARE_API_TOKEN")
    account = require_ident(
        "CLOUDFLARE_ACCOUNT_ID",
        os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""),
        ACCOUNT_RE,
    )
    zone = require_ident(
        "CLOUDFLARE_ZONE_ID",
        os.environ.get("CLOUDFLARE_ZONE_ID", ""),
        ACCOUNT_RE,
    )
    return token, account, zone


class Client:
    def __init__(self, api_base, api_token, account, zone, timeout, poll):
        self.api_base = api_base.rstrip("/")
        self.api_token = api_token
        self.account = account
        self.zone = zone
        self.timeout = timeout
        self.poll = poll

    def request(self, method, path, body=None):
        url = self.api_base + path
        data = None
        headers = {
            "Authorization": "Bearer " + self.api_token,
            "Accept": "application/json",
        }
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status = resp.status
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read()
        except urllib.error.URLError:
            raise ApiError(f"cloudflared: {method} {path} failed (network)") from None
        try:
            payload = json.loads(raw.decode()) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(f"cloudflared: {method} {path} failed ({status})") from None
        if status >= 400 or not isinstance(payload, dict) or not payload.get("success"):
            raise ApiError(fail_message(method, path, status, payload), status=status)
        return payload

    def each_page(self, path, query):
        page = 1
        while page <= 100:
            payload = self.request("GET", f"{path}?{query}&per_page=50&page={page}")
            result = payload.get("result")
            if not isinstance(result, list):
                raise ApiError("cloudflared: list is invalid")
            yield result
            info = (
                payload.get("result_info") if isinstance(payload.get("result_info"), dict) else {}
            )
            total = info.get("total_pages")
            try:
                total_pages = int(total) if total is not None else 1
            except (TypeError, ValueError):
                total_pages = 1
            if page >= max(total_pages, 1):
                return
            page += 1
        raise ApiError("cloudflared: list did not end")

    def list_tunnels(self, name=None):
        query = "is_deleted=false"
        if name is not None:
            query += "&name=" + urllib.parse.quote(name)
        found = []
        path = f"/accounts/{self.account}/cfd_tunnel"
        for result in self.each_page(path, query):
            for item in result:
                if isinstance(item, dict) and not item.get("deleted_at"):
                    found.append(item)
        return found

    def datum_tunnels(self):
        return [item for item in self.list_tunnels(name="datum") if item.get("name") == "datum"]

    def require_remote(self, tunnel):
        src = tunnel.get("config_src", "cloudflare")
        if src in (None, ""):
            src = "cloudflare"
        if src != "cloudflare":
            raise ApiError("cloudflared: tunnel datum is not remotely managed")

    def tunnel_id(self, value):
        require_ident("tunnel id", value, TUNNEL_RE)
        return value.lower()

    def one_datum(self, tunnels):
        if len(tunnels) > 1:
            raise ApiError("cloudflared: more than one tunnel named datum")
        if len(tunnels) != 1:
            return None
        self.require_remote(tunnels[0])
        return self.tunnel_id(tunnels[0].get("id"))

    def ensure_datum(self):
        tunnel_id = self.one_datum(self.datum_tunnels())
        if tunnel_id is not None:
            print(f"tunnel datum {tunnel_id}")
            return tunnel_id
        payload = self.request(
            "POST",
            f"/accounts/{self.account}/cfd_tunnel",
            {"name": "datum", "config_src": "cloudflare"},
        )
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        tunnel_id = self.tunnel_id(result.get("id"))
        print(f"created tunnel datum {tunnel_id}")
        return tunnel_id

    def find_datum(self):
        tunnel_id = self.one_datum(self.datum_tunnels())
        if tunnel_id is None:
            raise ApiError("cloudflared: tunnel datum is missing")
        return tunnel_id

    def get_config(self, tunnel_id):
        path = f"/accounts/{self.account}/cfd_tunnel/{tunnel_id}/configurations"
        try:
            payload = self.request("GET", path)
        except ApiError as exc:
            if exc.status == 404:
                return {}
            raise
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        config = result.get("config") if isinstance(result.get("config"), dict) else {}
        return config

    def put_config(self, tunnel_id, config):
        path = f"/accounts/{self.account}/cfd_tunnel/{tunnel_id}/configurations"
        self.request("PUT", path, {"config": config})

    def set_ingress(self, tunnel_id):
        config = self.get_config(tunnel_id)
        if config.get("ingress") == list(INGRESS):
            print("ingress unchanged")
            return
        updated = dict(config)
        updated["ingress"] = list(INGRESS)
        self.put_config(tunnel_id, updated)
        print("ingress set")

    def write_token(self, tunnel_id, path):
        url_path = f"/accounts/{self.account}/cfd_tunnel/{tunnel_id}/token"
        payload = self.request("GET", url_path)
        token = payload.get("result")
        if not token_ok(token):
            raise ApiError("cloudflared: connector token is empty or not a single line")
        line = f'TUNNEL_TOKEN="{token}"\n'.encode()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, line)
        finally:
            os.close(fd)

    def wait_healthy(self, tunnel_id):
        deadline = time.monotonic() + self.timeout
        path = f"/accounts/{self.account}/cfd_tunnel/{tunnel_id}"
        while True:
            payload = self.request("GET", path)
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            if result.get("status") == "healthy":
                return
            if time.monotonic() >= deadline:
                raise ApiError("cloudflared: tunnel datum is not healthy")
            time.sleep(self.poll)

    def dns_records(self):
        found = []
        path = f"/zones/{self.zone}/dns_records"
        query = "name=" + urllib.parse.quote(HOSTNAME)
        for result in self.each_page(path, query):
            found.extend(item for item in result if isinstance(item, dict))
        return found

    def record_points(self, record, tunnel_id):
        content = record.get("content")
        target = f"{tunnel_id}.cfargotunnel.com"
        return (
            record.get("type") == "CNAME"
            and record.get("proxied") is True
            and isinstance(content, str)
            and norm_host(content) == target
            and norm_host(record.get("name")) == HOSTNAME
        )

    def point_dns(self, tunnel_id):
        named = [item for item in self.dns_records() if norm_host(item.get("name")) == HOSTNAME]
        if any(self.record_points(item, tunnel_id) for item in named):
            print("dns unchanged")
            return
        if len(named) > 1:
            raise ApiError(f"cloudflared: {HOSTNAME} has more than one DNS record")
        body = {
            "type": "CNAME",
            "name": HOSTNAME,
            "content": f"{tunnel_id}.cfargotunnel.com",
            "proxied": True,
            "ttl": 1,
        }
        if len(named) == 1:
            record_id = require_ident("DNS record id", named[0].get("id"), RECORD_RE)
            path = f"/zones/{self.zone}/dns_records/{record_id}"
            self.request("PUT", path, body)
        else:
            self.request("POST", f"/zones/{self.zone}/dns_records", body)
        print("dns updated")

    def strip_other_ingress(self, datum_id):
        removed = False
        for tunnel in self.list_tunnels():
            tunnel_id = self.tunnel_id(tunnel.get("id"))
            if tunnel_id == datum_id:
                continue
            config = self.get_config(tunnel_id)
            ingress = config.get("ingress") if isinstance(config.get("ingress"), list) else []
            kept = [rule for rule in ingress if not is_datum_rule(rule)]
            if len(kept) == len(ingress):
                continue
            if not kept:
                kept = [{"service": "http_status:404"}]
            updated = dict(config)
            updated["ingress"] = kept
            self.put_config(tunnel_id, updated)
            print(f"removed {HOSTNAME} from tunnel {tunnel_id}")
            removed = True
        if not removed:
            print(f"no other tunnel has {HOSTNAME}")

    def wait_site(self, site_url):
        deadline = time.monotonic() + self.timeout
        opener = urllib.request.build_opener(NoRedirect)
        while True:
            code = 0
            req = urllib.request.Request(site_url, method="GET")
            try:
                with opener.open(req, timeout=30) as resp:
                    code = resp.status
                    resp.read()
            except urllib.error.HTTPError as exc:
                code = exc.code
                exc.read()
            except urllib.error.URLError:
                code = 0
            if code == 200:
                print("https ok")
                return
            if time.monotonic() >= deadline:
                raise ApiError("cloudflared: the site did not answer")
            time.sleep(self.poll)


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="cloudflare_tunnel.py")
    parser.add_argument("command", choices=("prepare", "finish"))
    parser.add_argument("--token-file")
    parser.add_argument("--api-base", default=os.environ.get("CLOUDFLARE_API_BASE", API_DEFAULT))
    parser.add_argument("--site-url", default=os.environ.get("CLOUDFLARE_SITE_URL", SITE_DEFAULT))
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("CLOUDFLARE_TIMEOUT", "120")),
    )
    parser.add_argument(
        "--poll",
        type=float,
        default=float(os.environ.get("CLOUDFLARE_POLL_SECONDS", "2")),
    )
    args = parser.parse_args(argv)
    if args.command == "prepare" and not args.token_file:
        parser.error("--token-file is required")
    if args.timeout <= 0 or args.poll < 0:
        parser.error("timeout must be positive")
    if not str(args.api_base).startswith(("https://", "http://")):
        parser.error("api base is invalid")
    if not str(args.site_url).startswith(("https://", "http://")):
        parser.error("site URL is invalid")
    return args


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        api_token, account, zone = load_secrets()
        client = Client(args.api_base, api_token, account, zone, args.timeout, args.poll)
        if args.command == "prepare":
            tunnel_id = client.ensure_datum()
            client.set_ingress(tunnel_id)
            client.write_token(tunnel_id, args.token_file)
        else:
            tunnel_id = client.find_datum()
            client.wait_healthy(tunnel_id)
            client.point_dns(tunnel_id)
            client.strip_other_ingress(tunnel_id)
            client.wait_site(args.site_url)
    except ApiError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
