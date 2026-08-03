import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from vitamine.app import app
from vitamine.cv_dates import UNKNOWN_CV_DATE, cv_date_sort_key, cv_end_date
from vitamine.paths import create_blank_database


class CvDateSortingTests(unittest.TestCase):
    def test_entry_editor_preserves_section_during_summary_refresh(self):
        script = (Path(__file__).resolve().parents[1] / "vitamine" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('const selectedEntrySection = $("#entrySection").value;', script)
        self.assertIn('$("#entrySection").value = selectedEntrySection;', script)
        self.assertIn("updateGrantStatusVisibility();\n  state.entryAutosave.pending", script)

    def test_end_date_parser_uses_end_of_imprecise_period(self):
        self.assertEqual(str(cv_end_date("2025")), "2025-12-31")
        self.assertEqual(str(cv_end_date("04/2025")), "2025-04-30")
        self.assertEqual(str(cv_end_date("2025-04-30")), "2025-04-30")
        self.assertIsNone(cv_end_date("Present"))

    def test_parser_recognizes_imported_and_manually_entered_date_formats(self):
        cases = {
            "04/01/18-10/31/21": (2018, 4, 1),
            "05/2025-04/2033": (2025, 5, 1),
            "2018-2023": (2018, 1, 1),
            "2018-04-01": (2018, 4, 1),
            "01.04.2018": (2018, 4, 1),
            "Apr 1, 2018": (2018, 4, 1),
            "April 2018": (2018, 4, 1),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(cv_date_sort_key(raw), expected)
        self.assertEqual(cv_date_sort_key("Present"), UNKNOWN_CV_DATE)

    def test_entries_api_orders_mixed_date_precision_chronologically(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "dates.vitamine"
            create_blank_database(database)
            rows = [
                ("04/01/18", "10/31/21", "2018 full date"),
                ("05/01/25", "", "2025 full date"),
                ("06/01/23", "04/30/25", "2023 full date"),
                ("11/01/21", "04/30/25", "2021 full date"),
                ("2015", "", "2015 year"),
                ("01/2026", "12/2029", "2026 month"),
                ("", "", "undated"),
            ]
            with sqlite3.connect(database) as con:
                con.executemany(
                    """
                    INSERT INTO cv_entries
                      (section_key, start_date, end_date, title, raw_text)
                    VALUES ('academic_appointments', ?, ?, ?, ?)
                    """,
                    [(start, end, title, title) for start, end, title in rows],
                )
                con.commit()

            with patch("vitamine.app.active_db_path", return_value=database):
                with TestClient(app) as client:
                    response = client.get("/api/entries", params={"section": "academic_appointments"})

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(
                [entry["title"] for entry in response.json()["entries"]],
                [
                    "2015 year",
                    "2018 full date",
                    "2021 full date",
                    "2023 full date",
                    "2025 full date",
                    "2026 month",
                    "undated",
                ],
            )

    def test_funding_statuses_migrate_and_expired_funded_grants_become_past(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "grants.vitamine"
            create_blank_database(database)
            with sqlite3.connect(database) as con:
                con.executemany(
                    """
                    INSERT INTO cv_entries (section_key, subcategory, end_date, title, raw_text)
                    VALUES ('funding', ?, ?, ?, ?)
                    """,
                    [
                        (None, "2020", "Old award", "Old award"),
                        ("grant_application", "2020", "Submitted proposal", "Submitted proposal"),
                        (None, "2099", "Current award", "Current award"),
                    ],
                )

            with patch("vitamine.app.active_db_path", return_value=database):
                with TestClient(app) as client:
                    response = client.get("/api/entries", params={"section": "funding"})

            self.assertEqual(response.status_code, 200, response.text)
            statuses = {entry["title"]: entry["grant_status"] for entry in response.json()["entries"]}
            self.assertEqual(statuses["Old award"], "past")
            self.assertEqual(statuses["Submitted proposal"], "past")
            self.assertEqual(statuses["Current award"], "funded")

    def test_entry_update_preserves_funding_section_and_forces_expired_status(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "grant-edit.vitamine"
            create_blank_database(database)
            with sqlite3.connect(database) as con:
                entry_id = con.execute(
                    "INSERT INTO cv_entries (section_key, grant_status, title, raw_text) "
                    "VALUES ('funding', 'funded', 'Edited grant', 'Edited grant')"
                ).lastrowid
                con.commit()

            payload = {
                "section_key": "funding", "grant_status": "submitted",
                "start_date": "2015", "end_date": "2017", "title": "Edited grant",
            }
            with patch("vitamine.app.active_db_path", return_value=database):
                with TestClient(app) as client:
                    response = client.put(f"/api/entries/{entry_id}", json=payload)
                    listed = client.get("/api/entries", params={"section": "funding"})

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["entry"]["section_key"], "funding")
            self.assertEqual(response.json()["entry"]["grant_status"], "past")
            self.assertEqual(listed.json()["entries"][0]["section_key"], "funding")
            self.assertEqual(listed.json()["entries"][0]["grant_status"], "past")

    def test_entry_translation_uses_configured_additional_language_in_both_directions(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "translation.vitamine"
            create_blank_database(database)
            with sqlite3.connect(database) as con:
                entry_id = con.execute(
                    "INSERT INTO cv_entries (section_key, title, organization, description, raw_text) "
                    "VALUES ('committee_service', 'Scientific Board', 'Example Foundation', 'Advisory role', 'Advisory role')"
                ).lastrowid
                con.execute(
                    "INSERT INTO app_settings (key, value) VALUES ('home_language_label', 'Italiano') "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
                )
                con.commit()

            calls = []

            def translate(prompt, _schema, _settings):
                calls.append(prompt)
                if "from English to Italiano" in prompt:
                    return {
                        "title": "Comitato scientifico", "organization": "Fondazione Example",
                        "location": "", "role": "", "description": "Ruolo consultivo",
                    }, None
                return {
                    "title": "Scientific Board", "organization": "Example Foundation",
                    "location": "", "role": "", "description": "Advisory role",
                }, None

            with patch("vitamine.app.active_db_path", return_value=database), patch("vitamine.app.llm_json", side_effect=translate):
                with TestClient(app) as client:
                    forward = client.post(
                        f"/api/entries/{entry_id}/translate", json={"direction": "primary_to_additional"}
                    )
                    reverse = client.post(
                        f"/api/entries/{entry_id}/translate", json={"direction": "additional_to_primary"}
                    )

            self.assertEqual(forward.status_code, 200, forward.text)
            self.assertEqual(forward.json()["entry"]["title_de"], "Comitato scientifico")
            self.assertEqual(reverse.status_code, 200, reverse.text)
            self.assertEqual(reverse.json()["target_language"], "English")
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
