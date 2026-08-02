import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vitamine.cloud_app import connect, ingest_llm_usage_events, initialize_database
from vitamine.llm_usage import usage_costs, usage_event
from vitamine.scripts.import_uploaded_cv import parse_json_response
from vitamine.scripts.manage_cloud import llm_usage_totals, premium_account_totals


class LlmUsageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.environment = patch.dict(
            os.environ,
            {
                "VITAMINE_CLOUD_DB": str(self.root / "cloud.sqlite"),
                "VITAMINE_DATABASE_URL": "",
                "VITAMINE_CLOUD_PEPPER": "usage-test-pepper",
            },
        )
        self.environment.start()
        initialize_database()
        with connect() as con:
            con.execute(
                "INSERT INTO invitations(id, label, code_hash, created_at) VALUES (1, 'test', 'hash', 'now')"
            )
            con.execute(
                "INSERT INTO members(id, invitation_id, created_at, last_seen_at) VALUES ('member-1', 1, 'now', 'now')"
            )
            con.execute(
                """
                INSERT INTO account_databases
                  (id, member_id, name, filename, sqlite_blob, checksum, size_bytes, created_at, updated_at)
                VALUES ('cv-1', 'member-1', 'CV', 'cv.vitamine', ?, 'sum', 1, 'now', 'now')
                """,
                (b"x",),
            )
            con.execute(
                """
                INSERT INTO background_jobs
                  (id, member_id, database_id, kind, status, base_revision, created_at, updated_at)
                VALUES ('job-1', 'member-1', 'cv-1', 'cv_import', 'running', 1, 'now', 'now')
                """
            )

    def tearDown(self):
        self.environment.stop()
        self.directory.cleanup()

    def test_complete_partial_and_missing_usage_are_safe(self):
        complete = usage_event(
            {
                "id": "response-1",
                "model": "gpt-4.1-mini",
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_tokens_details": {"cached_tokens": 30},
                    "completion_tokens_details": {"reasoning_tokens": 4},
                },
                "private": "PRIVATE-CV-CONTENT",
            }
        )
        self.assertEqual(complete["cached_input_tokens"], 30)
        self.assertEqual(complete["reasoning_tokens"], 4)
        self.assertNotIn("PRIVATE-CV-CONTENT", json.dumps(complete))
        self.assertIsNone(usage_event({"id": "response-2"}))
        partial = usage_event({"id": "response-3", "model": "model", "usage": {"input_tokens": 7}})
        self.assertEqual(partial["input_tokens"], 7)
        self.assertIsNone(partial["output_tokens"])
        costs = usage_costs({"model": "gpt-4.1-mini-2025-04-14", "input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 20})
        self.assertEqual(costs["wholesale_cost_microusd"], 60)
        self.assertEqual(costs["charged_cost_microusd"], 60)
        nano_costs = usage_costs(
            {"model": "gpt-5.4-nano-2026-03-17", "input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 20}
        )
        self.assertEqual(nano_costs["wholesale_cost_microusd"], 38)
        self.assertEqual(nano_costs["charged_cost_microusd"], 38)
        luna_costs = usage_costs(
            {"model": "gpt-5.6-luna-2026-07-30", "input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 20}
        )
        self.assertEqual(luna_costs["wholesale_cost_microusd"], 37)
        self.assertEqual(luna_costs["charged_cost_microusd"], 37)

    def test_response_capture_and_ingestion_are_idempotent(self):
        usage_path = self.root / "usage.jsonl"
        provider_payload = {
            "id": "response-repeat",
            "model": "gpt-4.1-mini",
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            "choices": [{"message": {"content": '{"person": {}}'}}],
        }
        with patch.dict(os.environ, {"VITAMINE_LLM_USAGE_PATH": str(usage_path)}):
            parse_json_response(json.dumps(provider_payload).encode())
            parse_json_response(json.dumps(provider_payload).encode())
        job = {"id": "job-1", "member_id": "member-1", "database_id": "cv-1", "kind": "cv_import"}
        self.assertEqual(ingest_llm_usage_events(job, usage_path), 1)
        self.assertEqual(ingest_llm_usage_events(job, usage_path), 0)
        with connect() as con:
            row = con.execute("SELECT * FROM llm_usage_events").fetchone()
            self.assertEqual(row["input_tokens"], 12)
            self.assertEqual(row["output_tokens"], 3)
            self.assertEqual(row["wholesale_cost_microusd"], 10)
            self.assertEqual(row["charged_cost_microusd"], 10)
        totals = llm_usage_totals(30)
        self.assertEqual(totals[0]["responses"], 1)
        self.assertEqual(totals[0]["input_tokens"], 12)
        self.assertEqual(totals[0]["charged_cost_microusd"], 10)
        accounts = premium_account_totals()
        self.assertEqual(accounts[0]["charged_microusd"], 10)
        self.assertEqual(accounts[0]["balance_microusd"], -10)


if __name__ == "__main__":
    unittest.main()
