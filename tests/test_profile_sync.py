import json
import sqlite3
import unittest

from vitamine.profile_sync import (
    ADD_REMOTE,
    ORCID,
    ZOTERO,
    REMOVE_REMOTE,
    complete_recommendations,
    ensure_profile_sync_tables,
    observe_remote_publications,
    pending_recommendations,
    prepare_recommendation_action,
    refresh_recommendations,
    skip_recommendations,
    zotero_service,
)


def database():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE publications (
          id INTEGER PRIMARY KEY, source TEXT, item_type TEXT, title TEXT, venue TEXT,
          year TEXT, doi TEXT, orcid_put_code TEXT, zotero_key TEXT
        );
        CREATE TABLE import_inbox_items (
          id INTEGER PRIMARY KEY, source TEXT, target_type TEXT, status TEXT, payload_json TEXT
        );
        """
    )
    ensure_profile_sync_tables(con)
    return con


class ProfileSyncTests(unittest.TestCase):
    def test_rejected_orcid_import_becomes_a_remove_recommendation(self):
        con = database()
        con.execute(
            "INSERT INTO import_inbox_items VALUES (1, 'orcid', 'publication', 'rejected', ?)",
            (json.dumps({"title": "Wrong paper", "doi": "10.1000/wrong", "orcid_put_code": "123"}),),
        )
        payload = pending_recommendations(con)
        self.assertEqual(payload["counts"][REMOVE_REMOTE], 1)
        item = payload["items"][0]
        self.assertEqual(item["payload"]["remote_id"], "123")
        prepared = prepare_recommendation_action(con, ORCID, REMOVE_REMOTE, [item["id"]])
        self.assertEqual(prepared[0]["payload"]["orcid_put_code"], "123")

    def test_doi_publication_missing_from_observed_orcid_becomes_add_recommendation(self):
        con = database()
        con.execute(
            "INSERT INTO publications VALUES (7, 'manual', 'journal-article', 'My paper', 'Journal', '2025', '10.1000/mine', '', '')"
        )
        observe_remote_publications(con, ORCID, [{"title": "Other paper", "doi": "10.1000/other", "orcid_put_code": "888"}])
        payload = pending_recommendations(con)
        self.assertEqual(payload["counts"][ADD_REMOTE], 1)
        item = payload["items"][0]
        self.assertEqual(item["publication_id"], 7)
        completed = complete_recommendations(con, ORCID, ADD_REMOTE, [item["id"]], {item["id"]: "999"})
        self.assertEqual(completed, 1)
        self.assertEqual(con.execute("SELECT orcid_put_code FROM publications WHERE id=7").fetchone()[0], "999")

    def test_observed_orcid_doi_does_not_create_duplicate_addition_and_skip_is_sticky(self):
        con = database()
        con.execute(
            "INSERT INTO publications VALUES (7, 'manual', 'journal-article', 'My paper', 'Journal', '2025', '10.1000/mine', '', '')"
        )
        observe_remote_publications(con, ORCID, [{"title": "My paper", "doi": "10.1000/mine", "orcid_put_code": "888"}])
        self.assertEqual(pending_recommendations(con)["total"], 0)
        con.execute("DELETE FROM profile_sync_remote_records")
        refresh_recommendations(con)
        item = pending_recommendations(con)["items"][0]
        self.assertEqual(skip_recommendations(con, ORCID, [item["id"]]), 1)
        self.assertEqual(pending_recommendations(con)["total"], 0)

    def test_zotero_suggestions_are_isolated_to_the_selected_collection(self):
        con = database()
        selected = zotero_service({"library_type": "groups", "library_id": "42", "source_mode": "collection", "collection_key": "NETSTIM"})
        other = zotero_service({"library_type": "groups", "library_id": "42", "source_mode": "collection", "collection_key": "OTHER"})
        self.assertIsNotNone(selected)
        con.execute(
            "INSERT INTO import_inbox_items VALUES (1, 'zotero', 'publication', 'rejected', ?)",
            (json.dumps({"title": "Wrong paper", "doi": "10.1000/wrong", "zotero_key": "ABC123", "profile_sync_service": selected}),),
        )
        con.execute(
            "INSERT INTO publications VALUES (7, 'manual', 'journalArticle', 'My paper', 'Journal', '2025', '10.1000/mine', '', '')"
        )
        observe_remote_publications(con, selected, [{"title": "Other", "doi": "10.1000/other", "zotero_key": "SELECTED"}])
        observe_remote_publications(con, other, [{"title": "My paper", "doi": "10.1000/mine", "zotero_key": "OTHER"}])
        payload = pending_recommendations(con, selected)
        self.assertEqual(payload["provider"], ZOTERO)
        self.assertEqual(payload["counts"][REMOVE_REMOTE], 1)
        self.assertEqual(payload["counts"][ADD_REMOTE], 1)
        self.assertEqual(pending_recommendations(con, other)["total"], 0)

    def test_zotero_addition_remembers_the_created_item_key(self):
        con = database()
        service = zotero_service({"library_type": "users", "library_id": "42", "source_mode": "my_publications"})
        con.execute(
            "INSERT INTO publications VALUES (7, 'manual', 'journalArticle', 'My paper', 'Journal', '2025', '10.1000/mine', '', '')"
        )
        observe_remote_publications(con, service, [])
        item = pending_recommendations(con, service)["items"][0]
        self.assertEqual(complete_recommendations(con, service, ADD_REMOTE, [item["id"]], {item["id"]: "ZOTERO1"}), 1)
        self.assertEqual(con.execute("SELECT zotero_key FROM publications WHERE id=7").fetchone()[0], "ZOTERO1")

    def test_zotero_whole_library_is_an_explicit_separate_source(self):
        whole_library = zotero_service({"library_type": "groups", "library_id": "42", "source_mode": "library"})
        collection = zotero_service({"library_type": "groups", "library_id": "42", "source_mode": "collection", "collection_key": "NETSTIM"})
        self.assertEqual(whole_library, "zotero:groups:42:library")
        self.assertNotEqual(whole_library, collection)


if __name__ == "__main__":
    unittest.main()
