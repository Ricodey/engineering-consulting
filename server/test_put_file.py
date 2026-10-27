import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "deploy.sh"
COREUTILS = Path("/usr/bin/coreutils")
TOKEN = "TUNNEL_TOKEN=abc+/=\n"
UNIT = (ROOT / "deploy" / "pod" / "datum-tunnel.container").read_text(encoding="utf-8")


def run(args, env, data=""):
    return subprocess.run(
        ["bash", str(DEPLOY), *args],
        cwd=ROOT,
        env=env,
        input=data,
        text=True,
        capture_output=True,
        check=False,
    )


def uutils_env(directory):
    bindir = Path(directory) / "uutils-bin"
    bindir.mkdir()
    for name in ("cat", "chmod", "dirname", "mktemp", "mv", "rm"):
        path = bindir / name
        path.write_text(
            '#!/bin/sh\nexec /usr/bin/coreutils %s "$@"\n' % name,
            encoding="utf-8",
        )
        path.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
    return env


class PutFileTests(unittest.TestCase):
    def test_deploy_path_does_not_install_from_stdin(self):
        paths = list((ROOT / "deploy").rglob("*.sh"))
        paths += list((ROOT / ".github" / "workflows").glob("*.yml"))
        self.assertGreaterEqual(len(paths), 4)
        for path in paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("/dev/stdin", text, path)

    def test_replaces_an_existing_file_twice(self):
        self.replace_twice(os.environ.copy())

    def test_replaces_an_existing_file_twice_with_uutils(self):
        if not COREUTILS.is_file():
            self.skipTest("rust-coreutils is not installed")
        version = subprocess.run(
            [str(COREUTILS), "cat", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertNotIn("GNU", version.stdout)
        with tempfile.TemporaryDirectory() as tmp:
            self.replace_twice(uutils_env(tmp))

    def replace_twice(self, env):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "tunnel.env"
            dest.write_text('TUNNEL_TOKEN="stale"\n', encoding="utf-8")
            dest.chmod(0o644)
            self.put("600", dest, TOKEN, env)
            self.assertEqual(dest.read_text(encoding="utf-8"), TOKEN)
            self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o600)
            again = "TUNNEL_TOKEN=second+/=value\n"
            self.put("600", dest, again, env)
            self.assertEqual(dest.read_text(encoding="utf-8"), again)
            self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o600)

            unit = Path(tmp) / "datum-tunnel.container"
            unit.write_text("old\n", encoding="utf-8")
            unit.chmod(0o600)
            self.put("644", unit, UNIT, env)
            self.assertEqual(unit.read_text(encoding="utf-8"), UNIT)
            self.assertEqual(stat.S_IMODE(unit.stat().st_mode), 0o644)
            self.put("644", unit, UNIT, env)
            self.assertEqual(unit.read_text(encoding="utf-8"), UNIT)
            self.assertEqual(stat.S_IMODE(unit.stat().st_mode), 0o644)
            self.assertEqual(list(Path(tmp).glob(".put.*")), [])

    def put(self, mode, dest, data, env):
        printed = run(["--print-put-command", mode, str(dest)], env)
        self.assertEqual(printed.returncode, 0, printed.stderr)
        self.assertNotIn("/dev/stdin", printed.stdout)
        command = printed.stdout.strip("\n")
        result = subprocess.run(
            ["bash", "-c", command],
            cwd=ROOT,
            env=env,
            input=data,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
