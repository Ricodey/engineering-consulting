import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "deploy.sh"
TOKEN_A = b"first+/=token"
TOKEN_B = b"abc+/=secret"


def run_deploy(*args, env, stdin=None):
    return subprocess.run(
        ["bash", str(DEPLOY), *args],
        cwd=ROOT,
        env=env,
        input=stdin,
        capture_output=True,
        check=False,
    )


class RedeploySecretTests(unittest.TestCase):
    def test_second_install_replaces_the_secret_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            store = Path(tmp) / "secret"
            log = Path(tmp) / "log"
            bin_dir.mkdir()
            stub = bin_dir / "podman"
            stub.write_text(
                "\n".join(
                    [
                        "#!/bin/sh",
                        "set -eu",
                        'printf "%s\\n" "$*" >>"$SECRET_LOG"',
                        'if [ "$*" != "secret create --replace raptorpath-cloudflared -" ]; then',
                        '  echo "refusing: $*" >&2',
                        "  exit 1",
                        "fi",
                        'cat >"$SECRET_STORE"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            stub.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
            env["SECRET_STORE"] = str(store)
            env["SECRET_LOG"] = str(log)
            env.pop("DEPLOY_HOST", None)

            first = run_deploy("--install-secret", env=env, stdin=TOKEN_A)
            self.assertEqual(first.returncode, 0, first.stderr.decode())
            self.assertEqual(store.read_bytes(), TOKEN_A)
            self.assertNotIn(TOKEN_A, first.stdout + first.stderr)

            second = run_deploy("--install-secret", env=env, stdin=TOKEN_B)
            self.assertEqual(second.returncode, 0, second.stderr.decode())
            self.assertEqual(store.read_bytes(), TOKEN_B)
            self.assertNotIn(b"TUNNEL_TOKEN=", store.read_bytes())
            self.assertNotIn(b'"', store.read_bytes())
            self.assertFalse(store.read_bytes().endswith(b"\n"))
            self.assertNotIn(TOKEN_B, second.stdout + second.stderr)
            self.assertEqual(
                log.read_text(encoding="utf-8").splitlines(),
                [
                    "secret create --replace raptorpath-cloudflared -",
                    "secret create --replace raptorpath-cloudflared -",
                ],
            )

            printed = run_deploy("--print-secret-command", env=env)
            self.assertEqual(printed.returncode, 0, printed.stderr.decode())
            script = printed.stdout.decode()
            self.assertIn("podman secret create --replace raptorpath-cloudflared -", script)
            self.assertNotIn("tunnel.env", script)
            helper = 'quoted=$(printf %q "$1"); ssh_cmd="bash -c $quoted"; bash -c "$ssh_cmd"'
            roundtrip = subprocess.run(
                ["bash", "-c", helper, "quote", script],
                cwd=ROOT,
                env=env,
                input=TOKEN_B,
                capture_output=True,
                check=False,
            )
            self.assertEqual(roundtrip.returncode, 0, roundtrip.stderr.decode())
            self.assertEqual(store.read_bytes(), TOKEN_B)
            self.assertNotIn(TOKEN_B, roundtrip.stdout + roundtrip.stderr)

    def test_install_secret_does_not_read_deploy_env(self):
        env = os.environ.copy()
        env["PATH"] = "/usr/bin:/bin"
        result = run_deploy("--print-secret-command", env=env)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertNotIn(b"deploy.env", result.stdout + result.stderr)

    def test_email_secret_replaces_stdin_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            store = Path(tmp) / "secret"
            log = Path(tmp) / "log"
            bin_dir.mkdir()
            stub = bin_dir / "podman"
            stub.write_text(
                "\n".join(
                    [
                        "#!/bin/sh",
                        "set -eu",
                        'printf "%s\\n" "$*" >>"$SECRET_LOG"',
                        'if [ "$*" != "secret create --replace raptorpath-email -" ]; then',
                        '  echo "refusing: $*" >&2',
                        "  exit 1",
                        "fi",
                        'cat >"$SECRET_STORE"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            stub.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
            env["SECRET_STORE"] = str(store)
            env["SECRET_LOG"] = str(log)
            env.pop("DEPLOY_HOST", None)
            payload = b"fixture-email-token"

            first = run_deploy("--install-email-secret", env=env, stdin=payload)
            self.assertEqual(first.returncode, 0, first.stderr.decode())
            self.assertEqual(store.read_bytes(), payload)
            self.assertFalse(store.read_bytes().endswith(b"\n"))
            self.assertNotIn(payload, first.stdout + first.stderr)

            printed = run_deploy("--print-email-secret-command", env=env)
            self.assertEqual(printed.returncode, 0, printed.stderr.decode())
            script = printed.stdout.decode()
            self.assertIn("podman secret create --replace raptorpath-email -", script)
            self.assertNotIn("form.env", script)
            self.assertNotIn(payload.decode(), script)

    def test_deploy_path_has_no_stdin_install(self):
        roots = (ROOT / "deploy", ROOT / ".github" / "workflows")
        for root in roots:
            for path in root.rglob("*"):
                if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                    continue
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("/dev/stdin", text, str(path))
        self.assertFalse((ROOT / "deploy" / "pod" / "raptorpath-tunnel.container").exists())
        deploy = DEPLOY.read_text(encoding="utf-8")
        for line in deploy.splitlines():
            if "tunnel.env" in line or "raptorpath-tunnel.container" in line or "form.env" in line:
                self.assertIn("rm -f", line)
