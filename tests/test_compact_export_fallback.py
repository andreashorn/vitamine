import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from vitamine.scripts.export_publication_selection import selected_or_fallback_publications
from vitamine.scripts.export_utils import sanitize_docx_compatibility_markup


class CompactExportFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(
            """
            CREATE TABLE person (id INTEGER PRIMARY KEY, full_name TEXT, display_name TEXT);
            INSERT INTO person VALUES (1, 'Andreas Horn', 'Andreas Horn');
            CREATE TABLE publications (
              id INTEGER PRIMARY KEY,
              authors TEXT,
              title TEXT,
              venue TEXT,
              year TEXT,
              category TEXT,
              include_short INTEGER DEFAULT 0,
              include_ultrashort INTEGER DEFAULT 0,
              selected_order INTEGER,
              short_selected_order INTEGER,
              ultrashort_selected_order INTEGER,
              suppress_display INTEGER DEFAULT 0,
              impact_factor REAL,
              openalex_cited_by_count INTEGER DEFAULT 0
            );
            """
        )

    def tearDown(self) -> None:
        self.con.close()

    def add_publication(self, row: tuple) -> None:
        self.con.execute(
            """INSERT INTO publications
               (id, authors, title, venue, year, category, include_short,
                include_ultrashort, impact_factor, openalex_cited_by_count)
               VALUES (?, ?, ?, 'Journal', ?, 'peer_reviewed', ?, ?, ?, 0)""",
            row,
        )

    def test_fallback_prefers_recent_high_impact_first_or_last_author_work(self) -> None:
        self.add_publication((1, "Andreas Horn, Co Author", "Recent strong", "2026", 0, 0, 12.0))
        self.add_publication((2, "Co Author, Andreas Horn", "Older strong", "2015", 0, 0, 12.0))
        self.add_publication((3, "Other Person, Co Author", "Recent highest IF", "2026", 0, 0, 40.0))
        rows = selected_or_fallback_publications(self.con, profile="ultrashort", limit=2)
        self.assertEqual([row["title"] for row in rows], ["Recent strong", "Older strong"])

    def test_explicit_selection_takes_precedence_over_fallback(self) -> None:
        self.add_publication((1, "Andreas Horn, Co Author", "Fallback candidate", "2026", 0, 0, 20.0))
        self.add_publication((2, "Other Person, Co Author", "Explicit choice", "2010", 1, 0, 1.0))
        rows = selected_or_fallback_publications(self.con, profile="short", limit=10)
        self.assertEqual([row["title"] for row in rows], ["Explicit choice"])


class DocxCompatibilityTests(unittest.TestCase):
    def test_sanitizer_removes_stale_ignorable_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "warning.docx"
            with zipfile.ZipFile(path, "w") as package:
                package.writestr(
                    "word/document.xml",
                    b'<w:document xmlns:w="urn:w" xmlns:mc="urn:mc" mc:Ignorable="w14 w15"><w:body/></w:document>',
                )
            sanitize_docx_compatibility_markup(path)
            with zipfile.ZipFile(path) as package:
                xml = package.read("word/document.xml")
            self.assertNotIn(b"Ignorable", xml)
            self.assertIn(b"<w:body/>", xml)


if __name__ == "__main__":
    unittest.main()
