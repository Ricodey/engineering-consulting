import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "Ricodey/engineering-consulting"


def text(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


class ReleaseWorkflowTests(unittest.TestCase):
    def test_published_release_sshes_as_datum(self):
        workflow = text(".github/workflows/release.yml")
        self.assertIn("types: [published]", workflow)
        self.assertIn("github.event.release.tag_name", workflow)
        self.assertIn("runs-on: ubuntu-latest", workflow)
        self.assertNotIn("self-hosted", workflow)
        self.assertIn("github.repository == 'Ricodey/engineering-consulting'", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("./deploy/deploy.sh", workflow)
        self.assertIn("secrets.DEPLOY_HOST", workflow)
        self.assertIn("secrets.DEPLOY_USER", workflow)
        self.assertIn("secrets.DEPLOY_SSH_PORT", workflow)
        self.assertIn("secrets.DEPLOY_SSH_KEY", workflow)
        self.assertIn("secrets.DEPLOY_SSH_KNOWN_HOSTS", workflow)
        self.assertIn("StrictHostKeyChecking=yes", text("deploy/deploy.sh"))
        self.assertIn('DEPLOY_USER}" != "datum"', workflow)
        self.assertIn("if: always()", workflow)
        self.assertIn("contents: read", workflow)
        self.assertNotIn("pull_request", workflow)
        self.assertNotIn("ufw", workflow)
        self.assertNotIn("sshd", workflow)
        self.assertNotIn("BEGIN OPENSSH", workflow)
        self.assertNotIn("BEGIN RSA", workflow)
        self.assertNotIn("ghp_", workflow)
        ci = text(".github/workflows/ci.yml")
        self.assertNotIn("setup-runner.sh", ci)
        self.assertNotIn("self-hosted", ci)
        self.assertIn("deploy/datum.sh", ci)
        self.assertIn("deploy/copy.sh", ci)

    def test_docs_describe_the_secrets(self):
        readme = text("README.md")
        agents = text("AGENTS.md")
        for doc in (readme, agents):
            self.assertIn(REPO, doc)
            self.assertIn("DEPLOY_SSH_KEY", doc)
            self.assertIn("DEPLOY_USER", doc)
        self.assertIn("/home/datum/form.env", readme)
        self.assertNotIn("RUNNER_TOKEN", readme)
        self.assertNotIn("/etc/datum/form.env", readme)
