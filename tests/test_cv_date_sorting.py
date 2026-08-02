import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from vitamine.app import app
from vitamine.cv_dates import UNKNOWN_CV_DATE, cv_date_sort_key
from vitamine.paths import create_blank_database


class CvDateSortingTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
