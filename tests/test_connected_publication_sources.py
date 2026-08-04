import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from vitamine.app import (
    ai_web_discovery_enabled,
    connected_publication_sources,
    discover_ai_profile_candidates,
    discover_researcher_profiles,
    enrich_cv_job,
)


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

    def test_researcher_profile_failure_does_not_abort_enrichment(self):
        con = database()
        con.execute("CREATE TABLE publications (title TEXT, year TEXT, doi TEXT, authors TEXT, venue TEXT, suppress_display INTEGER, id INTEGER)")
        con.execute("ALTER TABLE person ADD COLUMN full_name TEXT")
        with (
            patch("vitamine.app.connect", return_value=con),
            patch("vitamine.app.resolve_profiles", side_effect=RuntimeError("unexpected response")),
        ):
            result = discover_researcher_profiles()
        self.assertFalse(result["ok"])
        self.assertEqual(result["warnings"], ["Researcher profile discovery was skipped (RuntimeError)."])

    def test_web_profile_processing_failure_is_skipped_per_source(self):
        con = database()
        con.execute("ALTER TABLE person_identifiers ADD COLUMN identifier_type TEXT")
        con.execute("ALTER TABLE person_identifiers ADD COLUMN url TEXT")
        con.execute("CREATE TABLE import_inbox_items (status TEXT)")
        con.execute(
            "UPDATE person_identifiers SET platform='Lab page', identifier_type='Website', identifier_value='lab', url='https://example.org/profile' WHERE id=1"
        )
        if con.execute("SELECT count(*) FROM person_identifiers").fetchone()[0] == 0:
            con.execute(
                "INSERT INTO person_identifiers (person_id, platform, identifier_type, identifier_value, url) VALUES (1, 'Lab page', 'Website', 'lab', 'https://example.org/profile')"
            )
        with (
            patch("vitamine.app.connect", return_value=con),
            patch("vitamine.app.cv_import_settings", return_value={"provider": "openai"}),
            patch("vitamine.app.fetch_profile_text", return_value=("profile " * 80, "text/plain")),
            patch("vitamine.app.llm_extract", return_value=({"publications": []}, None)),
            patch("vitamine.app.ensure_discovery_document", side_effect=RuntimeError("unexpected database edge case")),
        ):
            result = discover_ai_profile_candidates()
        self.assertEqual(result["sources_checked"], 1)
        self.assertFalse(result["results"][0]["ok"])
        self.assertIn("Profile discovery was skipped (RuntimeError).", result["warnings"][0])

    def test_enrichment_returns_the_configured_policy(self):
        con = MagicMock()
        con.execute.return_value.fetchone.return_value = {
            "visible_publications": 1,
            "publications_with_citations": 1,
            "citation_total": 2,
        }
        connection = MagicMock()
        connection.__enter__.return_value = con
        script_result = SimpleNamespace(returncode=0, stdout="", stderr="")
        with (
            patch("vitamine.app.connect", return_value=connection),
            patch("vitamine.app.connected_publication_sources", return_value=()),
            patch("vitamine.app.run_script", return_value=script_result),
            patch("vitamine.app.reconcile_enrichment_changes", return_value={}),
            patch("vitamine.app.maintain", return_value={}),
            patch("vitamine.app.discover_researcher_profiles", return_value={"accepted": 0, "staged": 0}),
            patch("vitamine.app.discover_ai_profile_candidates", return_value={"candidates_staged": 0}),
            patch("vitamine.app.pending_inbox_count", return_value=0),
        ):
            result = enrich_cv_job()
        self.assertTrue(result["ok"])
        self.assertIn("provider", result["policy"])


if __name__ == "__main__":
    unittest.main()
