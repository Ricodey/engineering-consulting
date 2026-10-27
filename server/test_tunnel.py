import importlib.util
import json
import os
import stat
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "cloudflare_tunnel",
    ROOT / "deploy" / "cloudflare_tunnel.py",
)
tunnel = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tunnel)

ACCOUNT = "a" * 32
ZONE = "b" * 32
DATUM_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
OTHER_ID = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
EXTRA_ID = "cccccccc-dddd-4eee-8fff-111111111111"
RECORD_ID = "c" * 32
OTHER_RECORD = "d" * 32
API_TOKEN = "api-token-should-stay-hidden"
CONNECTOR = "connector-secret-value"
HOSTNAME = "datum.pancakeboard.com"
ORIGIN = "http://127.0.0.1:8080"
DESIRED = [
    {"hostname": HOSTNAME, "service": ORIGIN},
    {"service": "http_status:404"},
]


def datum_tunnel(tunnel_id=DATUM_ID):
    return {"id": tunnel_id, "name": "datum", "config_src": "cloudflare"}


def podman_env(text):
    """podman --env-file splits on the first '=' and does not strip quotes."""
    parsed = {}
    for raw in text.splitlines():
        if raw == "" or raw.startswith("#"):
            continue
        name, value = raw.split("=", 1)
        parsed[name] = value
    return parsed


def kept_rule():
    return {
        "hostname": "pancakeboard.com",
        "service": "http://127.0.0.1:9000",
        "originRequest": {"httpHostHeader": "pancakeboard.com"},
    }


def other_rule():
    return {
        "hostname": "other.pancakeboard.com",
        "service": "http://127.0.0.1:9001",
        "path": "/x",
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.route()

    def do_POST(self):
        self.route()

    def do_PUT(self):
        self.route()

    def do_DELETE(self):
        self.route()

    def route(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw.decode()) if raw else None
        parsed = urlparse(self.path)
        if parsed.path.startswith("/accounts") or parsed.path.startswith("/zones"):
            if self.headers.get("Authorization") != "Bearer " + self.server.api_token:
                self.server.auth_failures += 1
        self.server.calls.append({"method": self.command, "path": self.path, "body": body})
        status, payload = self.server.world.handle(self.command, self.path, body)
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        return


class World:
    def __init__(self):
        self.tunnels = []
        self.configs = {}
        self.records = []
        self.connector_token = CONNECTOR
        self.per_page = 50
        self.healthy_after = 1
        self.health_reads = 0
        self.site_status = 200
        self.site_hits = 0
        self.created = 0
        self.created_id = DATUM_ID
        self.config_status = 200
        self.config_served = False
        self.fail_list = False

    def handle(self, method, path, body):
        parsed = urlparse(path)
        parts = [part for part in parsed.path.split("/") if part]
        query = parse_qs(parsed.query)
        if parsed.path in ("/site", "/site/"):
            self.site_hits += 1
            return self.site_status, {"ok": True}
        if len(parts) >= 2 and parts[0] == "accounts" and parts[1] == ACCOUNT:
            return self.account(method, parts[2:], query, body)
        if len(parts) >= 2 and parts[0] == "zones" and parts[1] == ZONE:
            return self.zone(method, parts[2:], query, body)
        return 404, {"success": False, "errors": [{"message": "not found"}]}

    def account(self, method, parts, query, body):
        if self.fail_list and method == "GET" and parts == ["cfd_tunnel"]:
            return 403, {"success": False, "errors": [{"code": 9109, "message": "Unauthorized"}]}
        if parts == ["cfd_tunnel"] and method == "GET":
            return self.list_tunnels(query)
        if parts == ["cfd_tunnel"] and method == "POST":
            return self.create_tunnel(body or {})
        if len(parts) == 2 and parts[0] == "cfd_tunnel" and method == "GET":
            return self.one_tunnel(parts[1])
        if len(parts) == 3 and parts[0] == "cfd_tunnel" and parts[2] == "configurations":
            if method == "GET":
                return self.read_config(parts[1])
            if method == "PUT":
                return self.write_config(parts[1], body or {})
        if len(parts) == 3 and parts[0] == "cfd_tunnel" and parts[2] == "token" and method == "GET":
            return 200, {"success": True, "result": self.connector_token}
        return 404, {"success": False, "errors": [{"message": "not found"}]}

    def list_tunnels(self, query):
        items = list(self.tunnels)
        name = query.get("name", [None])[0]
        if name:
            items = [item for item in items if str(item.get("name", "")).startswith(name)]
        page = int(query.get("page", ["1"])[0])
        per = self.per_page
        start = (page - 1) * per
        chunk = items[start : start + per]
        total_pages = max(1, (len(items) + per - 1) // per)
        return 200, {
            "success": True,
            "result": chunk,
            "result_info": {"page": page, "per_page": per, "total_pages": total_pages},
        }

    def one_tunnel(self, tunnel_id):
        self.health_reads += 1
        for item in self.tunnels:
            if item.get("id") == tunnel_id:
                result = dict(item)
                if self.health_reads >= self.healthy_after:
                    result["status"] = "healthy"
                else:
                    result["status"] = "inactive"
                return 200, {"success": True, "result": result}
        return 404, {"success": False, "errors": [{"message": "not found"}]}

    def create_tunnel(self, body):
        created = {
            "id": self.created_id,
            "name": body.get("name"),
            "config_src": body.get("config_src"),
            "status": "healthy",
        }
        self.tunnels.append(created)
        self.created += 1
        return 200, {"success": True, "result": created}

    def read_config(self, tunnel_id):
        if self.config_status == 404 and tunnel_id == DATUM_ID and not self.config_served:
            self.config_served = True
            return 404, {
                "success": False,
                "errors": [{"code": 1000, "message": "configuration not found"}],
            }
        config = self.configs.get(tunnel_id, {"ingress": []})
        return 200, {"success": True, "result": {"tunnel_id": tunnel_id, "config": config}}

    def write_config(self, tunnel_id, body):
        self.configs[tunnel_id] = body.get("config") or {}
        return 200, {"success": True, "result": {"config": self.configs[tunnel_id]}}

    def zone(self, method, parts, query, body):
        del query
        if parts == ["dns_records"] and method == "GET":
            return 200, {
                "success": True,
                "result": list(self.records),
                "result_info": {"total_pages": 1},
            }
        if parts == ["dns_records"] and method == "POST":
            record = dict(body or {})
            record["id"] = RECORD_ID
            self.records.append(record)
            return 200, {"success": True, "result": record}
        if len(parts) == 2 and parts[0] == "dns_records" and method == "PUT":
            for record in self.records:
                if record.get("id") == parts[1]:
                    record.update(body or {})
                    return 200, {"success": True, "result": record}
            return 404, {"success": False, "errors": [{"message": "not found"}]}
        return 404, {"success": False, "errors": [{"message": "not found"}]}


class TunnelCutoverTests(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.world = self.world
        self.server.calls = []
        self.server.api_token = API_TOKEN
        self.server.auth_failures = 0
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{port}"
        self.site = self.base + "/site"
        names = ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_ZONE_ID")
        self._saved = {name: os.environ.get(name) for name in names}
        os.environ["CLOUDFLARE_API_TOKEN"] = API_TOKEN
        os.environ["CLOUDFLARE_ACCOUNT_ID"] = ACCOUNT
        os.environ["CLOUDFLARE_ZONE_ID"] = ZONE

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.server.shutdown()
        self.server.server_close()

    def execute(self, command, token_file=None, timeout="0.6"):
        args = [
            command,
            "--api-base",
            self.base,
            "--site-url",
            self.site,
            "--timeout",
            timeout,
            "--poll",
            "0.01",
        ]
        if token_file is not None:
            args.extend(["--token-file", token_file])
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = tunnel.main(args)
        leaked = stdout.getvalue() + stderr.getvalue() + json.dumps(self.server.calls)
        self.assertNotIn(API_TOKEN, leaked)
        if self.world.connector_token:
            self.assertNotIn(self.world.connector_token, leaked)
        self.assertEqual(self.server.auth_failures, 0)
        methods = [call["method"] for call in self.server.calls]
        self.assertNotIn("DELETE", methods)
        return code, stdout.getvalue(), stderr.getvalue()

    def posts(self, suffix):
        return [
            call
            for call in self.server.calls
            if call["method"] == "POST" and call["path"].split("?", 1)[0].endswith(suffix)
        ]

    def puts(self, suffix):
        return [
            call for call in self.server.calls if call["method"] == "PUT" and suffix in call["path"]
        ]

    def test_creates_tunnel_when_missing(self):
        with self.token_path() as path:
            code, stdout, stderr = self.execute("prepare", token_file=str(path))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.world.created, 1)
        created = self.posts("/cfd_tunnel")
        self.assertEqual(len(created), 1)
        self.assertEqual(set(created[0]["body"]), {"name", "config_src"})
        self.assertEqual(created[0]["body"]["name"], "datum")
        self.assertEqual(created[0]["body"]["config_src"], "cloudflare")
        written = self.puts("/configurations")
        self.assertEqual(written[0]["body"]["config"]["ingress"], DESIRED)
        self.assertIn(f"created tunnel datum {DATUM_ID}", stdout)
        self.assertIn("ingress set", stdout)
        self.assertIn(ORIGIN, json.dumps(written[0]["body"]))

    def test_reuses_existing_tunnel_and_ingress(self):
        self.world.tunnels = [datum_tunnel()]
        self.world.configs[DATUM_ID] = {"ingress": DESIRED}
        with self.token_path() as path:
            code, stdout, stderr = self.execute("prepare", token_file=str(path))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.posts("/cfd_tunnel"), [])
        self.assertEqual(self.puts("/configurations"), [])
        self.assertIn(f"tunnel datum {DATUM_ID}", stdout)
        self.assertNotIn("created tunnel", stdout)
        self.assertIn("ingress unchanged", stdout)

    def test_second_datum_tunnel_is_not_created(self):
        self.world.per_page = 1
        self.world.tunnels = [datum_tunnel(), datum_tunnel(OTHER_ID)]
        with self.token_path() as path:
            code, stdout, stderr = self.execute("prepare", token_file=str(path))
        self.assertNotEqual(code, 0)
        self.assertIn("more than one tunnel named datum", stderr)
        self.assertEqual(self.posts("/cfd_tunnel"), [])
        self.assertNotIn(API_TOKEN, stdout + stderr)

    def test_local_tunnel_is_not_replaced(self):
        local = datum_tunnel()
        local["config_src"] = "local"
        self.world.tunnels = [local]
        with self.token_path() as path:
            code, _stdout, stderr = self.execute("prepare", token_file=str(path))
        self.assertNotEqual(code, 0)
        self.assertIn("not remotely managed", stderr)
        self.assertEqual(self.posts("/cfd_tunnel"), [])

    def test_deleted_name_does_not_block_create(self):
        deleted = datum_tunnel(OTHER_ID)
        deleted["deleted_at"] = "2020-01-01T00:00:00Z"
        self.world.tunnels = [deleted]
        self.world.created_id = EXTRA_ID
        with self.token_path() as path:
            code, stdout, stderr = self.execute("prepare", token_file=str(path))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(len(self.posts("/cfd_tunnel")), 1)
        self.assertIn(f"created tunnel datum {EXTRA_ID}", stdout)
        token_gets = [call["path"] for call in self.server.calls if call["path"].endswith("/token")]
        self.assertEqual(token_gets, [f"/accounts/{ACCOUNT}/cfd_tunnel/{EXTRA_ID}/token"])

    def test_prefix_name_is_not_the_datum_tunnel(self):
        self.world.tunnels = [{"id": EXTRA_ID, "name": "datum-extra", "config_src": "cloudflare"}]
        with self.token_path() as path:
            code, _stdout, stderr = self.execute("prepare", token_file=str(path))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(len(self.posts("/cfd_tunnel")), 1)
        token_gets = [call["path"] for call in self.server.calls if call["path"].endswith("/token")]
        self.assertIn(DATUM_ID, token_gets[0])
        self.assertNotIn(EXTRA_ID, token_gets[0])

    def test_datum_tunnel_on_a_later_page_is_reused(self):
        self.world.per_page = 1
        self.world.tunnels = [
            {"id": EXTRA_ID, "name": "datum-extra", "config_src": "cloudflare"},
            datum_tunnel(),
        ]
        self.world.configs[DATUM_ID] = {"ingress": DESIRED}
        with self.token_path() as path:
            code, stdout, stderr = self.execute("prepare", token_file=str(path))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.posts("/cfd_tunnel"), [])
        self.assertIn(f"tunnel datum {DATUM_ID}", stdout)
        self.assertNotIn("created tunnel", stdout)

    def test_token_file_is_mode_600_and_not_printed(self):
        self.world.tunnels = [datum_tunnel()]
        self.world.configs[DATUM_ID] = {"ingress": DESIRED}
        with self.token_path() as path:
            path.write_text("replace-me\n", encoding="utf-8")
            path.chmod(0o644)
            code, stdout, stderr = self.execute("prepare", token_file=str(path))
            text = path.read_text(encoding="utf-8")
            mode = stat.S_IMODE(path.stat().st_mode)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(text, f"TUNNEL_TOKEN={CONNECTOR}\n")
        self.assertNotIn('"', text)
        self.assertEqual(podman_env(text)["TUNNEL_TOKEN"], CONNECTOR)
        self.assertEqual(mode, 0o600)
        self.assertNotIn(CONNECTOR, stdout + stderr)
        self.assertNotIn(API_TOKEN, text)

    def test_base64_token_is_written_bare(self):
        self.world.connector_token = "abc+/=secret"
        self.world.tunnels = [datum_tunnel()]
        self.world.configs[DATUM_ID] = {"ingress": DESIRED}
        with self.token_path() as path:
            code, stdout, stderr = self.execute("prepare", token_file=str(path))
            text = path.read_text(encoding="utf-8")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(text, "TUNNEL_TOKEN=abc+/=secret\n")
        self.assertNotIn('"', text)
        self.assertEqual(podman_env(text)["TUNNEL_TOKEN"], "abc+/=secret")
        quoted = podman_env('TUNNEL_TOKEN="abc+/=secret"\n')
        self.assertEqual(quoted["TUNNEL_TOKEN"], '"abc+/=secret"')
        self.assertNotIn("abc+/=secret", stdout + stderr)

    def test_rejected_token_is_not_printed(self):
        self.world.connector_token = 'uniqtokenquote"value'
        self.world.tunnels = [datum_tunnel()]
        self.world.configs[DATUM_ID] = {"ingress": DESIRED}
        with self.token_path() as path:
            code, stdout, stderr = self.execute("prepare", token_file=str(path))
            written = path.exists() and path.stat().st_size > 0
        self.assertNotEqual(code, 0)
        self.assertIn("connector token is empty or not a single line", stderr)
        self.assertNotIn("uniqtokenquote", stdout + stderr)
        self.assertFalse(written)

    def test_ingress_is_replaced_and_warp_routing_kept(self):
        self.world.tunnels = [datum_tunnel()]
        self.world.configs[DATUM_ID] = {
            "ingress": [
                {
                    "hostname": HOSTNAME,
                    "service": "http://127.0.0.1:8443",
                    "originRequest": {"noTLSVerify": True},
                }
            ],
            "warp-routing": {"enabled": False},
        }
        with self.token_path() as path:
            code, stdout, stderr = self.execute("prepare", token_file=str(path))
        self.assertEqual(code, 0, stderr)
        self.assertIn("ingress set", stdout)
        body = self.puts("/configurations")[0]["body"]["config"]
        self.assertEqual(body["ingress"], DESIRED)
        self.assertEqual(body["warp-routing"], {"enabled": False})

    def test_missing_configuration_is_written(self):
        self.world.config_status = 404
        with self.token_path() as path:
            code, _stdout, stderr = self.execute("prepare", token_file=str(path))
        self.assertEqual(code, 0, stderr)
        self.assertNotIn("1000", stderr)
        self.assertEqual(self.puts("/configurations")[0]["body"]["config"]["ingress"], DESIRED)

    def test_finish_strips_only_the_datum_rule(self):
        rule = kept_rule()
        sibling = other_rule()
        catch = {"service": "http_status:404"}
        self.world.tunnels = [
            datum_tunnel(),
            {"id": OTHER_ID, "name": "pancake", "config_src": "cloudflare"},
            {"id": EXTRA_ID, "name": "widget", "config_src": "cloudflare"},
        ]
        self.world.configs[DATUM_ID] = {"ingress": DESIRED}
        self.world.configs[OTHER_ID] = {
            "ingress": [
                rule,
                {"hostname": "Datum.Pancakeboard.com.", "service": ORIGIN},
                sibling,
                catch,
            ],
            "warp-routing": {"enabled": False},
            "originRequest": {"connectTimeout": 10},
        }
        self.world.configs[EXTRA_ID] = {"ingress": [rule, catch], "warp-routing": {"enabled": True}}
        self.world.records = [
            {
                "id": RECORD_ID,
                "type": "CNAME",
                "name": HOSTNAME,
                "content": f"{OTHER_ID}.cfargotunnel.com",
                "proxied": True,
                "ttl": 1,
            },
            {
                "id": OTHER_RECORD,
                "type": "CNAME",
                "name": "pancakeboard.com",
                "content": "origin.example",
                "proxied": True,
                "ttl": 1,
            },
        ]
        code, stdout, stderr = self.execute("finish")
        self.assertEqual(code, 0, stderr)
        self.assertIn("dns updated", stdout)
        self.assertIn(f"removed {HOSTNAME} from tunnel {OTHER_ID}", stdout)
        self.assertIn("https ok", stdout)
        self.assertEqual(self.posts("/cfd_tunnel"), [])
        datum_puts = self.puts(f"/{DATUM_ID}/configurations")
        self.assertEqual(datum_puts, [])
        extra_puts = self.puts(f"/{EXTRA_ID}/configurations")
        self.assertEqual(extra_puts, [])
        updated = self.puts(f"/{OTHER_ID}/configurations")
        self.assertEqual(len(updated), 1)
        config = updated[0]["body"]["config"]
        self.assertEqual(config["ingress"], [rule, sibling, catch])
        self.assertEqual(config["warp-routing"], {"enabled": False})
        self.assertEqual(config["originRequest"], {"connectTimeout": 10})
        self.assertEqual(self.world.configs[EXTRA_ID]["warp-routing"], {"enabled": True})
        dns_writes = self.puts("/dns_records")
        self.assertEqual(len(dns_writes), 1)
        self.assertIn(RECORD_ID, dns_writes[0]["path"])
        self.assertNotIn(OTHER_RECORD, dns_writes[0]["path"])
        self.assertEqual(dns_writes[0]["body"]["content"], f"{DATUM_ID}.cfargotunnel.com")
        self.assertEqual(dns_writes[0]["body"]["proxied"], True)
        self.assertEqual(dns_writes[0]["body"]["ttl"], 1)
        untouched = [item for item in self.world.records if item["id"] == OTHER_RECORD][0]
        self.assertEqual(untouched["content"], "origin.example")
        self.assertLess(self.index(self.is_health), self.index(self.is_dns))
        self.assertLess(self.index(self.is_dns), self.index(self.is_config_put))
        self.assertLess(self.index(self.is_config_put), self.index(self.is_site))

    def test_existing_dns_is_not_rewritten(self):
        self.world.tunnels = [datum_tunnel()]
        self.world.records = [
            {
                "id": RECORD_ID,
                "type": "CNAME",
                "name": HOSTNAME,
                "content": f"{DATUM_ID.upper()}.CFARGOTUNNEL.COM.",
                "proxied": True,
                "ttl": 120,
            }
        ]
        code, stdout, stderr = self.execute("finish")
        self.assertEqual(code, 0, stderr)
        self.assertIn("dns unchanged", stdout)
        self.assertEqual(self.puts("/dns_records"), [])
        self.assertEqual(self.posts("/dns_records"), [])
        self.assertEqual(self.world.records[0]["ttl"], 120)

    def test_missing_dns_is_created(self):
        self.world.tunnels = [datum_tunnel()]
        code, stdout, stderr = self.execute("finish")
        self.assertEqual(code, 0, stderr)
        self.assertIn("dns updated", stdout)
        created = self.posts("/dns_records")
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["body"]["type"], "CNAME")
        self.assertEqual(created[0]["body"]["name"], HOSTNAME)
        self.assertEqual(created[0]["body"]["content"], f"{DATUM_ID}.cfargotunnel.com")
        self.assertEqual(created[0]["body"]["proxied"], True)
        self.assertEqual(self.puts("/dns_records"), [])

    def test_missing_pancake_rule_makes_no_change(self):
        rule = kept_rule()
        self.world.tunnels = [
            datum_tunnel(),
            {"id": OTHER_ID, "name": "pancake", "config_src": "cloudflare"},
        ]
        self.world.configs[OTHER_ID] = {
            "ingress": [rule, {"service": "http_status:404"}],
            "warp-routing": {"enabled": False},
        }
        self.world.records = [
            {
                "id": RECORD_ID,
                "type": "CNAME",
                "name": HOSTNAME,
                "content": f"{DATUM_ID}.cfargotunnel.com",
                "proxied": True,
            }
        ]
        code, stdout, stderr = self.execute("finish")
        self.assertEqual(code, 0, stderr)
        self.assertIn(f"no other tunnel has {HOSTNAME}", stdout)
        self.assertEqual(self.puts("/configurations"), [])
        self.assertEqual(self.world.configs[OTHER_ID]["ingress"][0], rule)

    def test_sole_datum_rule_becomes_a_catch_all(self):
        self.world.tunnels = [
            datum_tunnel(),
            {"id": OTHER_ID, "name": "pancake", "config_src": "cloudflare"},
        ]
        self.world.configs[OTHER_ID] = {
            "ingress": [{"hostname": HOSTNAME, "service": "http://127.0.0.1:8443"}],
            "warp-routing": {"enabled": True},
        }
        self.world.records = [
            {
                "id": RECORD_ID,
                "type": "CNAME",
                "name": HOSTNAME,
                "content": f"{DATUM_ID}.cfargotunnel.com",
                "proxied": True,
            }
        ]
        code, stdout, stderr = self.execute("finish")
        self.assertEqual(code, 0, stderr)
        self.assertIn(f"removed {HOSTNAME} from tunnel {OTHER_ID}", stdout)
        config = self.puts("/configurations")[0]["body"]["config"]
        self.assertEqual(config["ingress"], [{"service": "http_status:404"}])
        self.assertEqual(config["warp-routing"], {"enabled": True})
        self.assertEqual(len(self.world.tunnels), 2)

    def test_unhealthy_tunnel_does_not_touch_dns(self):
        self.world.healthy_after = 100
        self.world.tunnels = [
            datum_tunnel(),
            {"id": OTHER_ID, "name": "pancake", "config_src": "cloudflare"},
        ]
        self.world.configs[OTHER_ID] = {"ingress": [{"hostname": HOSTNAME, "service": ORIGIN}]}
        code, stdout, stderr = self.execute("finish", timeout="0.2")
        self.assertNotEqual(code, 0)
        self.assertIn("not healthy", stderr)
        self.assertNotIn("dns", stdout)
        self.assertEqual(self.world.site_hits, 0)
        self.assertFalse(any("/zones/" in call["path"] for call in self.server.calls))
        self.assertEqual(self.puts("/"), [])

    def test_dns_waits_for_a_healthy_connection(self):
        self.world.healthy_after = 2
        self.world.tunnels = [
            datum_tunnel(),
            {"id": OTHER_ID, "name": "pancake", "config_src": "cloudflare"},
        ]
        self.world.configs[OTHER_ID] = {
            "ingress": [kept_rule(), {"hostname": HOSTNAME, "service": ORIGIN}],
            "warp-routing": {"enabled": False},
        }
        code, _stdout, stderr = self.execute("finish")
        self.assertEqual(code, 0, stderr)
        health = [index for index, call in enumerate(self.server.calls) if self.is_health(call)]
        self.assertGreaterEqual(len(health), 2)
        self.assertLess(health[1], self.index(self.is_dns))
        self.assertLess(self.index(self.is_dns), self.index(self.is_config_put))
        self.assertLess(self.index(self.is_config_put), self.index(self.is_site))

    def test_finish_does_not_create_a_tunnel(self):
        code, _stdout, stderr = self.execute("finish", timeout="0.2")
        self.assertNotEqual(code, 0)
        self.assertIn("tunnel datum is missing", stderr)
        self.assertEqual(self.posts("/cfd_tunnel"), [])
        self.assertEqual(self.world.site_hits, 0)

    def test_rerun_does_not_duplicate_tunnel_dns_or_rules(self):
        self.world.per_page = 1
        self.world.tunnels = [{"id": OTHER_ID, "name": "pancake", "config_src": "cloudflare"}]
        self.world.configs[OTHER_ID] = {
            "ingress": [
                {"hostname": "pancakeboard.com", "service": "http://127.0.0.1:9000"},
                {"hostname": HOSTNAME, "service": "http://127.0.0.1:8443"},
                {"service": "http_status:404"},
            ],
            "warp-routing": {"enabled": False},
        }
        with self.token_path() as path:
            first, first_out, first_err = self.execute("prepare", token_file=str(path))
            self.assertEqual(first, 0, first_err)
            cut, _cut_out, cut_err = self.execute("finish")
            self.assertEqual(cut, 0, cut_err)
            again, again_out, again_err = self.execute("prepare", token_file=str(path))
            self.assertEqual(again, 0, again_err)
            repeat, repeat_out, repeat_err = self.execute("finish")
        self.assertEqual(repeat, 0, repeat_err)
        self.assertIn("created tunnel datum", first_out)
        self.assertNotIn("created tunnel", again_out)
        self.assertIn("ingress unchanged", again_out)
        self.assertIn("dns unchanged", repeat_out)
        self.assertIn(f"no other tunnel has {HOSTNAME}", repeat_out)
        self.assertEqual(len(self.posts("/cfd_tunnel")), 1)
        self.assertEqual(len(self.posts("/dns_records")), 1)
        self.assertEqual(len(self.puts("/configurations")), 2)
        ingress = self.world.configs[OTHER_ID]["ingress"]
        self.assertEqual(
            ingress,
            [
                {"hostname": "pancakeboard.com", "service": "http://127.0.0.1:9000"},
                {"service": "http_status:404"},
            ],
        )
        self.assertEqual(self.world.configs[OTHER_ID]["warp-routing"], {"enabled": False})
        self.assertTrue(
            all(
                item.get("name") != "datum" or item["id"] == DATUM_ID for item in self.world.tunnels
            )
        )
        self.assertEqual(sum(item.get("name") == "datum" for item in self.world.tunnels), 1)

    def test_two_dns_records_are_not_rewritten(self):
        self.world.tunnels = [datum_tunnel()]
        self.world.records = [
            {
                "id": RECORD_ID,
                "type": "A",
                "name": HOSTNAME,
                "content": "192.0.2.10",
                "proxied": True,
            },
            {
                "id": OTHER_RECORD,
                "type": "CNAME",
                "name": HOSTNAME,
                "content": "old.example",
                "proxied": True,
            },
        ]
        before = json.dumps(self.world.records)
        code, _stdout, stderr = self.execute("finish", timeout="0.2")
        self.assertNotEqual(code, 0)
        self.assertIn("more than one DNS record", stderr)
        self.assertEqual(json.dumps(self.world.records), before)
        self.assertEqual(self.puts("/dns_records"), [])
        self.assertEqual(self.posts("/dns_records"), [])

    def test_redirect_is_not_treated_as_up(self):
        self.world.site_status = 302
        self.world.tunnels = [datum_tunnel()]
        self.world.records = [
            {
                "id": RECORD_ID,
                "type": "CNAME",
                "name": HOSTNAME,
                "content": f"{DATUM_ID}.cfargotunnel.com",
                "proxied": True,
            }
        ]
        code, stdout, stderr = self.execute("finish", timeout="0.05")
        self.assertNotEqual(code, 0)
        self.assertNotIn("https ok", stdout)
        self.assertIn("did not answer", stderr)
        self.assertEqual(self.puts("/dns_records"), [])

    def test_invalid_account_id_is_not_echoed(self):
        os.environ["CLOUDFLARE_ACCOUNT_ID"] = "pasted-token-not-an-id"
        with self.token_path() as path:
            code, stdout, stderr = self.execute("prepare", token_file=str(path))
        self.assertNotEqual(code, 0)
        self.assertIn("CLOUDFLARE_ACCOUNT_ID is invalid", stderr)
        self.assertNotIn("pasted-token-not-an-id", stdout + stderr)
        self.assertEqual(self.server.calls, [])

    def test_api_error_omits_the_raw_body(self):
        self.world.fail_list = True
        with self.token_path() as path:
            code, _stdout, stderr = self.execute("prepare", token_file=str(path))
        self.assertNotEqual(code, 0)
        self.assertIn("Unauthorized", stderr)
        self.assertNotIn("9109", stderr)
        self.assertNotIn('"code"', stderr)

    def index(self, predicate):
        for index, call in enumerate(self.server.calls):
            if predicate(call):
                return index
        self.fail("missing call")

    @staticmethod
    def is_health(call):
        path = call["path"].split("?", 1)[0]
        return call["method"] == "GET" and path.endswith("/cfd_tunnel/" + DATUM_ID)

    @staticmethod
    def is_dns(call):
        return "/zones/" in call["path"]

    @staticmethod
    def is_config_put(call):
        return call["method"] == "PUT" and call["path"].endswith("/configurations")

    @staticmethod
    def is_site(call):
        return call["path"].startswith("/site")

    def token_path(self):
        return TokenPath()


class TokenPath:
    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        return Path(self._tmp.name) / "tunnel.env"

    def __exit__(self, exc_type, exc, tb):
        self._tmp.cleanup()
        return False


class QuadletPinTests(unittest.TestCase):
    def test_image_pin_keeps_host_ports_unbound(self):
        pod_dir = ROOT / "deploy" / "pod"
        unit = (pod_dir / "datum-tunnel.container").read_text(encoding="utf-8")
        pod = (pod_dir / "datum.pod").read_text(encoding="utf-8")
        nginx = (pod_dir / "nginx.conf").read_text(encoding="utf-8")
        source = (ROOT / "deploy" / "cloudflare_tunnel.py").read_text(encoding="utf-8")
        self.assertIn("Image=docker.io/cloudflare/cloudflared:2026.10.4", unit)
        self.assertIn("Exec=tunnel --no-autoupdate run", unit)
        self.assertNotIn("PublishPort=", unit)
        self.assertNotIn("--token", unit)
        self.assertNotIn("Network=host", unit)
        self.assertIn("PublishPort=127.0.0.1:8443:8080", pod)
        self.assertNotRegex(pod + unit, r":80\b")
        self.assertNotRegex(pod + unit, r":443\b")
        self.assertNotRegex(nginx, r"listen\s+80\b")
        self.assertNotRegex(nginx, r"listen\s+443\b")
        self.assertNotIn("DELETE", source)
        self.assertNotIn("tunnel_secret", source)
        self.assertNotIn("import requests", source)
        self.assertEqual(tunnel.ORIGIN, ORIGIN)


if __name__ == "__main__":
    unittest.main()
