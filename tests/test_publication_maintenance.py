import sqlite3
import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from vitamine.app import list_publications
from vitamine.paths import create_blank_database
from vitamine.scripts.maintain_publications import (
    classify_and_suppress_non_cv_publications,
    ensure_columns,
    suppress_definite_duplicates,
    suppress_orcid_without_researcher_authorship,
)
from vitamine.scripts.sync_orcid import normalize_doi, normalize_title


def database() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE person (
          id INTEGER PRIMARY KEY,
          full_name TEXT,
          display_name TEXT,
          orcid_id TEXT
        );
        INSERT INTO person VALUES (1, 'Andreas Georg Rudolf Horn', 'Andreas Horn', '0000-0002-0695-6025');
        CREATE TABLE publications (
          id INTEGER PRIMARY KEY,
          source TEXT,
          item_type TEXT,
          category TEXT,
          authors TEXT,
          title TEXT,
          venue TEXT,
          year TEXT,
          doi TEXT,
          raw_citation TEXT,
          extra TEXT
        );
        """
    )
    ensure_columns(con)
    return con


class PublicationMaintenanceTests(unittest.TestCase):
    def test_orcid_normalizers_handle_urls_and_typography(self):
        self.assertEqual(normalize_doi("https://doi.org/10.1000/Example"), "10.1000/example")
        self.assertEqual(
            normalize_title("A title: with punctuation – and spacing"),
            "a title with punctuation and spacing",
        )

    def test_exact_doi_duplicate_is_suppressed(self):
        con = database()
        con.executescript(
            """
            INSERT INTO publications
              (id, source, item_type, category, authors, title, venue, year, doi)
            VALUES
              (1, 'zotero', 'journalArticle', 'peer_reviewed', 'Andreas Horn', 'Same paper', 'Brain', '2024', '10.1000/x'),
              (2, 'orcid', 'journal-article', 'peer_reviewed', '', 'Same paper', 'Brain', '2024', 'https://doi.org/10.1000/X');
            """
        )
        self.assertEqual(suppress_definite_duplicates(con), 1)
        row = con.execute("SELECT suppress_display, quality_note FROM publications WHERE id=2").fetchone()
        self.assertEqual(row["suppress_display"], 1)
        self.assertIn("DOI duplicate", row["quality_note"])

    def test_blank_orcid_authors_are_suppressed(self):
        con = database()
        con.execute(
            """
            INSERT INTO publications
              (id, source, item_type, category, authors, title, venue, year, doi)
            VALUES
              (1, 'orcid', 'journal-article', 'peer_reviewed', '', 'Unverified paper', 'Journal', '2024', '')
            """
        )
        self.assertEqual(suppress_orcid_without_researcher_authorship(con), 1)
        self.assertEqual(
            con.execute("SELECT suppress_display FROM publications WHERE id=1").fetchone()[0],
            1,
        )

    def test_preprints_are_categorized_without_being_hidden(self):
        con = database()
        con.execute(
            """
            INSERT INTO publications
              (id, source, item_type, category, authors, title, venue, year, doi,
               suppress_display, include_short, include_ultrashort, quality_note)
            VALUES
              (1, 'zotero', 'preprint', 'preprints', 'Andreas Horn', 'Visible preprint',
               'bioRxiv', '2026', '', 1, 1, 1,
               'Suppressed preprint; not exported to CV publication lists.')
            """
        )

        self.assertEqual(classify_and_suppress_non_cv_publications(con), 1)
        row = con.execute(
            "SELECT category, suppress_display, include_short, include_ultrashort, quality_note FROM publications WHERE id=1"
        ).fetchone()
        self.assertEqual(row["category"], "preprints")
        self.assertEqual(row["suppress_display"], 0)
        self.assertEqual(row["include_short"], 0)
        self.assertEqual(row["include_ultrashort"], 0)
        self.assertIsNone(row["quality_note"])

    def test_corrections_remain_hidden(self):
        con = database()
        con.execute(
            """
            INSERT INTO publications
              (id, source, item_type, category, authors, title, venue, year, doi)
            VALUES
              (1, 'zotero', 'journalArticle', 'peer_reviewed', 'Andreas Horn',
               'Correction: Original paper', 'Brain', '2026', '10.1000/correction')
            """
        )

        self.assertEqual(classify_and_suppress_non_cv_publications(con), 1)
        row = con.execute("SELECT suppress_display, quality_note FROM publications WHERE id=1").fetchone()
        self.assertEqual(row["suppress_display"], 1)
        self.assertIn("correction/erratum", row["quality_note"])

    def test_publication_list_shows_legacy_auto_suppressed_preprints(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preprints.vitamine"
            create_blank_database(path)
            with sqlite3.connect(path) as con:
                con.execute(
                    """
                    INSERT INTO publications
                      (id, source, item_type, category, authors, title, venue, year, doi, raw_citation,
                       suppress_display, quality_note)
                    VALUES
                      (1, 'zotero', 'preprint', 'preprints', 'Andreas Horn', 'Legacy preprint',
                       'bioRxiv', '2026', '', 'Legacy preprint', 1,
                       'Suppressed preprint; not exported to CV publication lists.'),
                      (2, 'manual', 'preprint', 'preprints', 'Andreas Horn', 'Manually hidden preprint',
                       'bioRxiv', '2026', '', 'Manually hidden preprint', 1, 'Hidden by user.')
                    """
                )
            with patch("vitamine.app.active_db_path", return_value=path):
                payload = list_publications()

        self.assertEqual([row["title"] for row in payload["publications"]], ["Legacy preprint"])


if __name__ == "__main__":
    unittest.main()
