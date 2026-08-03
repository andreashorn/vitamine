import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vitamine.paths import create_blank_database
from vitamine.scripts.build_long_cv import entry_rows
from vitamine.scripts.build_short_cv import load_data
from vitamine.scripts.build_ultrashort_tabular_cv import row_by_title


class GrantExportVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "grants.vitamine"
        create_blank_database(self.database)
        with sqlite3.connect(self.database) as con:
            for status in ("planned", "submitted", "rejected", "funded", "past"):
                con.execute(
                    "INSERT INTO cv_entries "
                    "(section_key, grant_status, title, raw_text, include_long, include_short) "
                    "VALUES ('funding', ?, ?, ?, 1, 1)",
                    (status, f"{status.title()} grant", f"{status.title()} grant"),
                )
            con.commit()

    def test_long_export_includes_only_funded_and_past_grants(self):
        with sqlite3.connect(self.database) as con:
            con.row_factory = sqlite3.Row
            titles = [row["title"] for row in entry_rows(con, "funding")]
        self.assertEqual(titles, ["Funded grant", "Past grant"])

    def test_short_export_includes_only_funded_and_past_grants(self):
        with patch("vitamine.scripts.build_short_cv.DB", self.database):
            _person, entries, _publications = load_data()
        self.assertEqual([row["title"] for row in entries], ["Funded grant", "Past grant"])

    def test_one_page_lookup_cannot_select_non_exportable_grants(self):
        with sqlite3.connect(self.database) as con:
            con.row_factory = sqlite3.Row
            self.assertIsNone(row_by_title(con, "funding", "Submitted grant"))
            self.assertIsNone(row_by_title(con, "funding", "Rejected grant"))
            self.assertIsNotNone(row_by_title(con, "funding", "Funded grant"))


if __name__ == "__main__":
    unittest.main()
