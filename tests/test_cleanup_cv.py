import csv
import io
import json
import sqlite3
import unittest

from vitamine.app import (
    apply_cleanup_suggestion,
    attach_cleanup_preview,
    ensure_cleanup_change_log_table,
    ensure_import_inbox_table,
)
from vitamine.cleanup_cv import CLEANUP_CSV_COLUMNS, parse_cleanup_csv, run_cleanup_review


class CleanupCvTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(
            """
            CREATE TABLE person (id INTEGER PRIMARY KEY, full_name TEXT, display_name TEXT, degrees TEXT,
              position_title TEXT, work_email TEXT, orcid_id TEXT, own_institution_name TEXT);
            INSERT INTO person VALUES (1, 'Ada Lovelace', '', '', '', '', '', '');
            CREATE TABLE cv_entries (id INTEGER PRIMARY KEY, section_key TEXT, start_date TEXT, end_date TEXT,
              title TEXT, organization TEXT, location TEXT, role TEXT, amount TEXT, description TEXT, raw_text TEXT);
            CREATE TABLE publications (id INTEGER PRIMARY KEY, authors TEXT, title TEXT, venue TEXT, year TEXT,
              doi TEXT, pmid TEXT, url TEXT, raw_citation TEXT, quality_note TEXT);
            INSERT INTO publications VALUES (7, 'Lovelace, A.', 'A paper', 'ANNALS OF NEUROLOGY', '2026',
              '10.1000/example', '', '', 'Lovelace, A. A paper. ANNALS OF NEUROLOGY.', '');
            CREATE TABLE biosketch_contributions (id INTEGER PRIMARY KEY, ordinal INTEGER, title TEXT, narrative TEXT);
            CREATE TABLE documents (id INTEGER PRIMARY KEY, title TEXT);
            """
        )
        ensure_import_inbox_table(self.con)
        ensure_cleanup_change_log_table(self.con)

    def tearDown(self):
        self.con.close()

    def cleanup_csv(self, row):
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=CLEANUP_CSV_COLUMNS)
        writer.writeheader()
        writer.writerow(row)
        return output.getvalue()

    def test_cleanup_review_stages_a_csv_backed_venue_case_suggestion(self):
        csv_text = self.cleanup_csv(
            {
                "operation": "edit", "record_type": "publication", "record_id": "7", "field": "venue",
                "old_text": "ANNALS OF NEUROLOGY", "new_text": "Annals of Neurology",
                "related_record_type": "", "related_record_id": "", "rationale": "Normalize venue casing.", "confidence": "high",
            }
        )

        result = run_cleanup_review(
            self.con,
            lambda _prompt, _schema, _settings: ({"csv": csv_text}, None),
            {"provider": "openai"},
        )

        self.assertEqual(result["suggestions_staged"], 1)
        item = self.con.execute("SELECT * FROM import_inbox_items WHERE source='cv_cleanup'").fetchone()
        self.assertIn("ANNALS OF NEUROLOGY → Annals of Neurology", item["raw_text"])
        payload = json.loads(item["payload_json"])
        self.assertEqual(payload["locator"], {"record_type": "publication", "record_id": "7", "field": "venue"})

    def test_apply_cleanup_edit_updates_record_and_logs_orcid_pending(self):
        suggestion = {
            "operation": "edit", "record_type": "publication", "record_id": "7", "field": "venue",
            "old_text": "ANNALS OF NEUROLOGY", "new_text": "Annals of Neurology",
            "related_record_type": "", "related_record_id": "", "rationale": "Normalize venue casing.", "confidence": "high",
        }
        self.con.execute(
            """INSERT INTO import_inbox_items
                 (source, target_type, status, confidence, title, payload_json)
                 VALUES ('cv_cleanup', 'cleanup_suggestion', 'pending', 'high', 'Edit venue', ?)""",
            (json.dumps({"cleanup_csv": suggestion}),),
        )
        row = self.con.execute("SELECT * FROM import_inbox_items").fetchone()
        status, record_id = apply_cleanup_suggestion(self.con, {**dict(row), "payload": {"cleanup_csv": suggestion}})

        self.assertEqual((status, record_id), ("accepted", 7))
        self.assertEqual(self.con.execute("SELECT venue FROM publications WHERE id=7").fetchone()[0], "Annals of Neurology")
        log = self.con.execute("SELECT operation, orcid_sync_status FROM cleanup_change_log").fetchone()
        self.assertEqual(tuple(log), ("edit", "pending"))

    def test_csv_parser_rejects_stale_old_text(self):
        rows = [{"record_type": "publication", "record_id": "7", "fields": {"venue": "ANNALS OF NEUROLOGY"}}]
        csv_text = self.cleanup_csv(
            {
                "operation": "edit", "record_type": "publication", "record_id": "7", "field": "venue",
                "old_text": "Different title", "new_text": "Annals of Neurology", "related_record_type": "",
                "related_record_id": "", "rationale": "", "confidence": "high",
            }
        )
        self.assertEqual(parse_cleanup_csv(csv_text, rows), [])

    def test_existing_cleanup_suggestion_receives_a_live_record_preview(self):
        item = {
            "target_type": "cleanup_suggestion",
            "payload": {"cleanup_csv": {"operation": "edit", "record_type": "publication", "record_id": "7"}},
        }
        previewed = attach_cleanup_preview(self.con, item)
        self.assertEqual(previewed["payload"]["record_preview"]["record_id"], 7)
        self.assertEqual(previewed["payload"]["record_preview"]["fields"]["venue"], "ANNALS OF NEUROLOGY")


if __name__ == "__main__":
    unittest.main()
