import sqlite3
import unittest

from vitamine.app import consolidate_person_identifiers, persist_discovered_identifiers
from vitamine.identifiers import identifier_value_from_url, normalize_identifier


class IdentifierPersistenceTests(unittest.TestCase):
    def test_orcid_discovered_identifiers_are_copied_without_duplication(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript(
            """
            CREATE TABLE person (id INTEGER PRIMARY KEY, orcid_id TEXT);
            INSERT INTO person (id, orcid_id) VALUES (1, '0000-0001-2345-6789');
            CREATE TABLE person_identifiers (
              id INTEGER PRIMARY KEY,
              person_id INTEGER NOT NULL,
              platform TEXT NOT NULL,
              identifier_type TEXT NOT NULL,
              identifier_value TEXT,
              url TEXT NOT NULL,
              source TEXT NOT NULL,
              verified_at TEXT,
              notes TEXT,
              UNIQUE(person_id, platform, identifier_type, identifier_value)
            );
            INSERT INTO person_identifiers
              (person_id, platform, identifier_type, identifier_value, url, source)
            VALUES
              (1, 'ORCID', 'ORCID iD', '0000-0001-2345-6789',
               'https://orcid.org/0000-0001-2345-6789', 'manual');
            """
        )
        rows = [
            {
                "platform": "ORCID",
                "identifier_type": "ORCID iD",
                "identifier_value": "0000-0001-2345-6789",
                "url": "https://orcid.org/0000-0001-2345-6789?source=public-record",
                "source": "manual",
            },
            {
                "platform": "Scopus",
                "identifier_type": "Author ID",
                "identifier_value": "123",
                "url": "https://www.scopus.com/authid/detail.uri?authorId=123",
                "source": "orcid",
            },
        ]
        self.assertEqual(persist_discovered_identifiers(con, rows), 1)
        self.assertEqual(persist_discovered_identifiers(con, rows), 0)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM person_identifiers").fetchone()[0], 2)

    def test_aliases_merge_and_url_only_profile_gets_a_value(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript(
            """
            CREATE TABLE person (id INTEGER PRIMARY KEY, orcid_id TEXT);
            INSERT INTO person (id, orcid_id) VALUES (1, '');
            CREATE TABLE person_identifiers (
              id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL, platform TEXT NOT NULL,
              identifier_type TEXT NOT NULL, identifier_value TEXT, url TEXT NOT NULL,
              source TEXT NOT NULL, verified_at TEXT, notes TEXT,
              UNIQUE(person_id, platform, identifier_type, identifier_value)
            );
            INSERT INTO person_identifiers
              (person_id, platform, identifier_type, identifier_value, url, source, verified_at)
            VALUES
              (1, 'Bluesky', 'Bluesky', NULL, 'https://bsky.app/profile/example.org', 'orcid-sync', '2026-01-01'),
              (1, 'Bluesky', 'Bluesky handle', 'example.org', 'https://bsky.app/profile/example.org', 'orcid-sync', '2026-02-01'),
              (1, 'Scopus Author ID', 'Scopus Author ID', '123', 'https://www.scopus.com/authid/detail.uri?authorId=123', 'orcid-sync', '2026-02-01'),
              (1, 'Scopus', 'Author ID', '123', 'https://www.scopus.com/authid/detail.uri?authorId=123', 'orcid-sync', '2026-01-01');
            """
        )
        self.assertEqual(consolidate_person_identifiers(con), 2)
        rows = con.execute(
            "SELECT platform, identifier_value FROM person_identifiers ORDER BY platform"
        ).fetchall()
        self.assertEqual([(row["platform"], row["identifier_value"]) for row in rows], [
            ("Bluesky", "example.org"),
            ("Scopus", "123"),
        ])

    def test_known_profile_values_are_extracted_conservatively(self):
        self.assertEqual(
            identifier_value_from_url("Google Scholar", "https://scholar.google.com/citations?user=abc-123&hl=en"),
            "abc-123",
        )
        self.assertEqual(
            identifier_value_from_url("ResearchGate", "https://www.researchgate.net/profile/Andreas-Horn"),
            "Andreas-Horn",
        )
        self.assertEqual(identifier_value_from_url("Lab Website", "https://example.org/about"), "")

    def test_google_scholar_regional_url_is_stored_on_main_domain(self):
        row = normalize_identifier(
            {
                "platform": "Google Scholar",
                "url": "http://scholar.google.co.uk/citations?hl=en&user=q_4u0aoAAAAJ&view_op=list_works",
            }
        )
        self.assertEqual(row["identifier_value"], "q_4u0aoAAAAJ")
        self.assertEqual(row["url"], "https://scholar.google.com/citations?user=q_4u0aoAAAAJ")


if __name__ == "__main__":
    unittest.main()
