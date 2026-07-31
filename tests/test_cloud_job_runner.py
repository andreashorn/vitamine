import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vitamine.cloud_app import create_blank_workspace_database
from vitamine.cloud_job_runner import execute


class CloudJobRunnerTests(unittest.TestCase):
    def test_cv_import_job_writes_candidates_and_a_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "workspace.vitamine"
            payload = root / "payload.json"
            result = root / "result.json"
            progress = root / "progress.json"
            preferences = root / "preferences.json"
            uploads = root / "uploads"
            uploads.mkdir()
            create_blank_workspace_database(database)
            (uploads / "1-cv.txt").write_text(
                "Curriculum Vitae\nEducation\n2020–2024 Example University — Researcher\n",
                encoding="utf-8",
            )
            payload.write_text(
                json.dumps(
                    {
                        "files": [
                            {
                                "stored_name": "1-cv.txt",
                                "original_name": "cv.txt",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            preferences.write_text(
                json.dumps({"cv_import": {"provider": "none"}}),
                encoding="utf-8",
            )
            desktop_profile = (
                Path(__file__).resolve().parents[1] / "config" / "vitamine-desktop.json"
            )
            with patch.dict(
                os.environ,
                {
                    "VITAMINE_DB": str(database),
                    "VITAMINE_DATA": str(root / "data"),
                    "VITAMINE_OUTPUT": str(root / "output"),
                    "VITAMINE_PREFERENCES": str(preferences),
                    "VITAMINE_DEPLOYMENT_CONFIG": str(desktop_profile),
                    "VITAMINE_CLOUD_WORKER": "1",
                },
            ):
                self.assertEqual(
                    execute(
                        kind="cv_import",
                        database_path=database,
                        payload_path=payload,
                        result_path=result,
                        progress_path=progress,
                    ),
                    0,
                )
            outcome = json.loads(result.read_text(encoding="utf-8"))
            self.assertTrue(outcome["ok"])
            self.assertGreater(outcome["candidates_staged"], 0)
            with sqlite3.connect(database) as con:
                staged = con.execute(
                    "SELECT COUNT(*) FROM import_inbox_items WHERE status='pending'"
                ).fetchone()[0]
            self.assertGreater(staged, 0)


if __name__ == "__main__":
    unittest.main()
