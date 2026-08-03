import sqlite3
import unittest

from vitamine.app import ai_web_discovery_enabled, connected_publication_sources


def database(*, zotero_key: str = "", orcid_id: str = "") -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE person (id INTEGER PRIMARY KEY, orcid_id TEXT);
        CREATE TABLE person_identifiers (
          id INTEGER PRIMARY KEY,
          person_id INTEGER,
          platform TEXT,
          identifier_value TEXT
        );
        INSERT INTO person (id, orcid_id) VALUES (1, '');
        """
    )
    if zotero_key:
        con.execute("INSERT INTO app_settings (key, value) VALUES ('zotero_api_key', ?)", (zotero_key,))
    if orcid_id:
        con.execute(
            "INSERT INTO person_identifiers (person_id, platform, identifier_value) VALUES (1, 'ORCID', ?)",
            (orcid_id,),
        )
    return con


class ConnectedPublicationSourcesTests(unittest.TestCase):
    def test_uses_every_connected_source(self):
        con = database(zotero_key="secret", orcid_id="0000-0002-0695-6025")
        self.assertEqual(connected_publication_sources(con), ("zotero", "orcid"))

    def test_does_not_run_unconfigured_sources(self):
        self.assertEqual(connected_publication_sources(database()), ())
        self.assertEqual(connected_publication_sources(database(orcid_id="0000-0002-0695-6025")), ("orcid",))
        self.assertEqual(connected_publication_sources(database(zotero_key="secret")), ("zotero",))

    def test_profile_discovery_is_always_enabled(self):
        con = database()
        con.execute("INSERT INTO app_settings (key, value) VALUES ('ai_web_discovery_enabled', '0')")
        self.assertTrue(ai_web_discovery_enabled(con))


if __name__ == "__main__":
    unittest.main()
