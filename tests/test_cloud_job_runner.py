import json
import os
import signal
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from vitamine.cloud_app import JOB_STOP, create_blank_workspace_database, run_background_job_runner
from vitamine.cloud_job_runner import execute
from vitamine.scripts import run_cloud_jobs


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


class HostedCloudJobServiceTests(unittest.TestCase):
    def setUp(self):
        JOB_STOP.clear()

    def tearDown(self):
        JOB_STOP.clear()

    def test_runner_initializes_recovers_then_starts_loop(self):
        root = MagicMock()
        with (
            patch("vitamine.cloud_app.initialize_database") as initialize,
            patch("vitamine.cloud_app.job_root", return_value=root),
            patch("vitamine.cloud_app.recover_background_jobs") as recover,
            patch("vitamine.cloud_app.background_job_loop") as loop,
        ):
            run_background_job_runner()

        initialize.assert_called_once_with()
        root.mkdir.assert_called_once_with(parents=True, exist_ok=True, mode=0o700)
        recover.assert_called_once_with()
        loop.assert_called_once_with()

    def test_signal_handler_requests_graceful_requeue(self):
        run_cloud_jobs.request_shutdown(signal.SIGTERM, None)
        self.assertTrue(JOB_STOP.is_set())

    def test_main_installs_shutdown_handlers_before_starting_runner(self):
        with (
            patch("vitamine.scripts.run_cloud_jobs.signal.signal") as register_signal,
            patch("vitamine.scripts.run_cloud_jobs.run_background_job_runner") as run,
        ):
            run_cloud_jobs.main()

        self.assertEqual(
            register_signal.call_args_list,
            [
                call(signal.SIGTERM, run_cloud_jobs.request_shutdown),
                call(signal.SIGINT, run_cloud_jobs.request_shutdown),
            ],
        )
        run.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
