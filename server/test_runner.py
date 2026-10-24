import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "Ricodey/engineering-consulting"


def text(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


class RunnerSetupTests(unittest.TestCase):
    def test_runner_is_repo_scoped_and_unprivileged(self):
        script = text("deploy/setup-runner.sh")
        self.assertIn(f"https://github.com/{REPO}", script)
        self.assertIn("runner_name=datum", script)
        self.assertIn("runner_label=datum", script)
        self.assertIn("--labels", script)
        self.assertIn("--work _work", script)
        self.assertIn("./svc.sh install deploy", script)
        self.assertIn("RUNNER_TOKEN", script)
        self.assertIn("/etc/datum/deploy-user", script)
        self.assertIn("actions.runner.*.service", script)
        self.assertNotIn("ghp_", script)
        self.assertNotIn("github_pat_", script)
        self.assertNotIn("pancakeboard", script)
        self.assertNotIn("ufw", script)
        self.assertNotIn("sshd", script)
        self.assertNotIn("iptables", script)
        self.assertNotIn("nft ", script)
        self.assertNotIn("443", script)
        self.assertNotIn("sudoers", script)
        for line in script.splitlines():
            if "systemctl" not in line:
                continue
            self.assertNotIn("nginx", line)
            self.assertNotIn("ssh", line)

    def test_runner_package_is_pinned(self):
        script = text("deploy/setup-runner.sh")
        self.assertIn("runner_version=2.338.1", script)
        self.assertIn(
            "08e9ccb3604d2462c24b5e33604dc30810296ca15cc65f23cffd4317eb144c37",
            script,
        )
        self.assertIn(
            "475e0c149fd3b611610127b9664ba5bacf5c6928795cc448ab520bb5f324bede",
            script,
        )


class ReleaseWorkflowTests(unittest.TestCase):
    def test_published_release_runs_deploy(self):
        workflow = text(".github/workflows/release.yml")
        self.assertIn("types: [published]", workflow)
        self.assertIn("github.event.release.tag_name", workflow)
        self.assertIn("runs-on: [self-hosted, datum]", workflow)
        self.assertIn("github.repository == 'Ricodey/engineering-consulting'", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("./deploy/deploy.sh", workflow)
        self.assertIn("secrets.DEPLOY_HOST", workflow)
        self.assertIn("secrets.DEPLOY_USER", workflow)
        self.assertIn("secrets.DEPLOY_SSH_PORT", workflow)
        self.assertIn("secrets.DEPLOY_SSH_KEY", workflow)
        self.assertIn('DEPLOY_USER}" != "deploy"', workflow)
        self.assertIn("if: always()", workflow)
        self.assertIn("contents: read", workflow)
        self.assertNotIn("pull_request", workflow)
        self.assertNotIn("ufw", workflow)
        self.assertNotIn("sshd", workflow)
        self.assertNotIn("BEGIN OPENSSH", workflow)
        self.assertNotIn("BEGIN RSA", workflow)
        self.assertNotIn("ghp_", workflow)
        ci = text(".github/workflows/ci.yml")
        self.assertIn("setup-runner.sh", ci)
        self.assertNotIn("self-hosted", ci)

    def test_docs_describe_the_token_and_secrets(self):
        readme = text("README.md")
        agents = text("AGENTS.md")
        for doc in (readme, agents):
            self.assertIn("setup-runner.sh", doc)
            self.assertIn("RUNNER_TOKEN", doc)
            self.assertIn(REPO, doc)
        self.assertIn("actions/runners/registration-token", readme)
        self.assertIn("DEPLOY_SSH_KEY", readme)
        self.assertIn("/etc/datum/form.env", readme)
        self.assertIn("does not listen", readme)
