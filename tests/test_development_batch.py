from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER = PROJECT_ROOT / "scripts" / "run_development_batch.sh"


class DevelopmentBatchRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        temporary_root = Path(self.temporary.name)
        self.repo = temporary_root / "repo"
        self.repo.mkdir()
        (self.repo / "scripts").mkdir()
        (self.repo / "docs").mkdir()
        shutil.copy2(RUNNER, self.repo / "scripts" / RUNNER.name)
        (self.repo / "docs" / "development-plan.md").write_text(
            "\n".join(
                [
                    "# Test plan",
                    "",
                    "### AUTO-001 [ ] - First safe task",
                    "",
                    "Evidence: pending",
                    "",
                    "### AUTO-002 [ ] - Second safe task",
                    "",
                    "Evidence: pending",
                    "",
                    "### AUTO-003 [ ] - Third safe task",
                    "",
                    "Evidence: pending",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self.run_git("init", "-b", "main")
        self.run_git("config", "user.name", "VitaMine Test")
        self.run_git("config", "user.email", "vitamine@example.invalid")
        self.run_git("add", ".")
        self.run_git("commit", "-m", "Initial test fixture")

        self.fake_bin = temporary_root / "fake-bin"
        self.fake_bin.mkdir()
        fake_codex = self.fake_bin / "codex"
        fake_codex.write_text(
            """#!/usr/bin/env bash
seen_exec=0
for argument in "$@"; do
  if [[ "$argument" == "exec" ]]; then
    seen_exec=1
  elif ((seen_exec)) && [[ "$argument" == "--ask-for-approval" ]]; then
    echo "approval option appeared after exec" >&2
    exit 64
  fi
done
exit 0
""",
            encoding="utf-8",
        )
        fake_codex.chmod(0o755)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_git(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=True,
        )

    def run_runner(
        self,
        *arguments: str,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "PATH": f"{self.fake_bin}:{os.environ.get('PATH', '')}"}
        env.update(extra_env or {})
        return subprocess.run(
            ["bash", "scripts/run_development_batch.sh", *arguments],
            cwd=self.repo,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_list_reports_only_pending_automation_tasks(self) -> None:
        result = self.run_runner("--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("AUTO-001", result.stdout)
        self.assertIn("AUTO-002", result.stdout)

    def test_refuses_default_branch(self) -> None:
        result = self.run_runner("--max", "1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("refuse to run automation directly", result.stderr)

    def test_stops_when_agent_does_not_commit_or_check_off_task(self) -> None:
        self.run_git("switch", "-c", "agent/test-batch")
        result = self.run_runner("--max", "1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("did not create the required commit", result.stderr)
        plan = (self.repo / "docs" / "development-plan.md").read_text(encoding="utf-8")
        self.assertIn("AUTO-001 [ ]", plan)

    def test_refuses_dirty_worktree_before_invoking_codex(self) -> None:
        self.run_git("switch", "-c", "agent/test-dirty")
        (self.repo / "uncommitted.txt").write_text("do not touch\n", encoding="utf-8")
        result = self.run_runner("--max", "1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("worktree must be clean", result.stderr)

    def test_rejects_invalid_maximum(self) -> None:
        result = self.run_runner("--max", "0")
        self.assertEqual(result.returncode, 2)
        self.assertIn("positive integer", result.stderr)

    def test_accepts_explicit_codex_binary_outside_path(self) -> None:
        self.run_git("switch", "-c", "agent/test-explicit-codex")
        explicit_codex = Path(self.temporary.name) / "explicit-codex"
        explicit_codex.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        explicit_codex.chmod(0o755)
        result = self.run_runner(
            "--max",
            "1",
            extra_env={
                "PATH": os.environ.get("PATH", ""),
                "VITAMINE_CODEX_BIN": str(explicit_codex),
            },
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("did not create the required commit", result.stderr)
        self.assertNotIn("Codex CLI was not found", result.stderr)

    def test_runs_tasks_sequentially_and_requires_local_commits(self) -> None:
        self.run_git("switch", "-c", "agent/test-success")
        fake_codex = self.fake_bin / "codex"
        fake_codex.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
prompt="${!#}"
task_id="$(printf '%s\\n' "$prompt" | sed -n 's/^Implement exactly task \\(AUTO-[0-9][0-9][0-9]\\).*/\\1/p' | sed -n '1p')"
python3 - "$task_id" <<'PY'
import re
import sys
from pathlib import Path

task_id = sys.argv[1]
plan_path = Path("docs/development-plan.md")
plan = plan_path.read_text(encoding="utf-8")
plan = plan.replace(f"### {task_id} [ ] - ", f"### {task_id} [x] - ", 1)
pattern = rf"(### {task_id} \\[x\\].*?Evidence:) pending"
plan, count = re.subn(pattern, rf"\\1 fixture validation passed", plan, count=1, flags=re.S)
if count != 1:
    raise SystemExit("evidence line not found")
plan_path.write_text(plan, encoding="utf-8")
PY
git add docs/development-plan.md
git commit -m "$task_id: fixture completion" >/dev/null
""",
            encoding="utf-8",
        )
        fake_codex.chmod(0o755)

        result = self.run_runner("--max", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = (self.repo / "docs" / "development-plan.md").read_text(encoding="utf-8")
        self.assertIn("AUTO-001 [x]", plan)
        self.assertIn("AUTO-002 [x]", plan)
        self.assertIn("AUTO-003 [ ]", plan)
        commit_subjects = self.run_git("log", "-2", "--format=%s").stdout.splitlines()
        self.assertEqual(
            commit_subjects,
            ["AUTO-002: fixture completion", "AUTO-001: fixture completion"],
        )
        self.assertEqual(self.run_git("status", "--porcelain").stdout, "")


if __name__ == "__main__":
    unittest.main()
