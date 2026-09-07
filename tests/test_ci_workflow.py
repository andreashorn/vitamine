from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"


class ContinuousIntegrationWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    def test_workflow_is_valid_yaml_when_parser_is_available(self) -> None:
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed; structural regressions still run.")
        document = yaml.compose(self.workflow)
        self.assertIsNotNone(document)

    def test_workflow_runs_for_pull_requests_and_main_pushes(self) -> None:
        self.assertRegex(self.workflow, r"(?m)^  pull_request:\s*$")
        self.assertRegex(
            self.workflow,
            r"(?ms)^  push:\s*\n    branches:\s*\n      - main\s*$",
        )

    def test_workflow_has_read_only_permissions_and_cancels_stale_runs(self) -> None:
        self.assertRegex(
            self.workflow,
            r"(?ms)^permissions:\s*\n  contents: read\s*$",
        )
        self.assertRegex(
            self.workflow,
            r"(?ms)^concurrency:.*?^  cancel-in-progress: true\s*$",
        )
        self.assertIn("persist-credentials: false", self.workflow)

    def test_workflow_installs_project_and_runs_complete_suite(self) -> None:
        self.assertIn('python-version: "3.11"', self.workflow)
        self.assertIn("cache: pip", self.workflow)
        self.assertIn("cache-dependency-path: pyproject.toml", self.workflow)
        self.assertIn("python -m pip install --editable .", self.workflow)
        self.assertIn("python -m unittest discover -s tests", self.workflow)

    def test_external_actions_are_pinned_to_commit_shas(self) -> None:
        action_references = re.findall(r"(?m)^\s+uses:\s+([^#\s]+)", self.workflow)
        self.assertGreaterEqual(len(action_references), 2)
        for reference in action_references:
            with self.subTest(reference=reference):
                self.assertRegex(reference, r"^[^@]+@[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
