import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.audit_repository import audit_paths, repository_files


class RepositoryAuditTests(unittest.TestCase):
    def audit_fixture(self, files):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(content, bytes):
                    path.write_bytes(content)
                else:
                    path.write_text(content, encoding="utf-8")
                paths.append(path)
            return {(finding.rule, finding.path) for finding in audit_paths(paths, root)}

    def test_safe_fixture_and_narrow_examples_pass(self):
        findings = self.audit_fixture(
            {
                "README.md": "No credentials here.",
                "data/example.vitamine": b"SQLite format 3\0synthetic",
                "deploy/strato/vitamine-cloud.env.example": (
                    "OPENAI_API_KEY=replace-me\n"
                    "ORCID_OAUTH_CLIENT_SECRET=replace-me\n"
                ),
            }
        )
        self.assertEqual(findings, set())

    def test_forbidden_paths_are_reported_by_rule_and_path(self):
        findings = self.audit_fixture(
            {
                ".env": "SAFE=value",
                "private/workspace.vitamine": b"SQLite format 3\0private",
                ".DS_Store": b"metadata",
                "keys/id_ed25519": "not even key material",
            }
        )
        self.assertIn(("ENV_FILE", ".env"), findings)
        self.assertIn(("PRIVATE_DATABASE", "private/workspace.vitamine"), findings)
        self.assertIn(("FINDER_METADATA", ".DS_Store"), findings)
        self.assertIn(("PRIVATE_KEY_FILE", "keys/id_ed25519"), findings)

    def test_high_confidence_credentials_and_keys_are_detected(self):
        findings = self.audit_fixture(
            {
                "notes.txt": (
                    "OPENAI_API_KEY=" + "sk-" + "proj-" + "abcdefghijklmnopqrstuvwxyz123456\n"
                    "-----BEGIN " + "OPENSSH PRIVATE KEY-----\n"
                ),
                "config.txt": (
                    "ORCID_OAUTH_CLIENT_SECRET=" + "actual-secret-value-12345\n"
                ),
            }
        )
        self.assertIn(("OPENAI_API_KEY", "notes.txt"), findings)
        self.assertIn(("PRIVATE_KEY_MATERIAL", "notes.txt"), findings)
        self.assertIn(("CREDENTIAL_ASSIGNMENT", "config.txt"), findings)

    def test_repository_listing_includes_tracked_and_proposed_but_not_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
            (root / "tracked.txt").write_text("tracked", encoding="utf-8")
            (root / "proposed.txt").write_text("proposed", encoding="utf-8")
            (root / "ignored.txt").write_text("ignored", encoding="utf-8")
            subprocess.run(
                ["git", "add", ".gitignore", "tracked.txt"],
                cwd=root,
                check=True,
            )
            paths = {path.relative_to(root).as_posix() for path in repository_files(root)}
            self.assertIn("tracked.txt", paths)
            self.assertIn("proposed.txt", paths)
            self.assertNotIn("ignored.txt", paths)


if __name__ == "__main__":
    unittest.main()
