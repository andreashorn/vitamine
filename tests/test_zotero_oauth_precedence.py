import os
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from vitamine.app import zotero_runtime_api_key, zotero_saved_env

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vitamine" / "scripts"))
from vitamine.scripts.sync_zotero import zotero_env


def connection() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
    con.executemany(
        "INSERT INTO app_settings(key, value) VALUES (?, ?)",
        (
            ("zotero_api_key", "legacy-manual-key"),
            ("zotero_library_type", "groups"),
            ("zotero_library_id", "6789"),
        ),
    )
    return con


class ZoteroOauthPrecedenceTests(unittest.TestCase):
    def test_cloud_worker_prefers_account_oauth_key_over_embedded_manual_key(self):
        con = connection()
        with patch.dict(
            os.environ,
            {"VITAMINE_CLOUD_WORKER": "1", "ZOTERO_API_KEY": "account-oauth-key"},
        ):
            self.assertEqual(zotero_runtime_api_key(con), "account-oauth-key")
            self.assertEqual(zotero_saved_env(con)["api_key"], "account-oauth-key")
            self.assertEqual(zotero_env(con)["ZOTERO_API_KEY"], "account-oauth-key")

    def test_desktop_keeps_explicit_database_key_precedence(self):
        con = connection()
        with patch.dict(
            os.environ,
            {"VITAMINE_CLOUD_WORKER": "", "ZOTERO_API_KEY": "environment-key"},
        ):
            self.assertEqual(zotero_runtime_api_key(con), "legacy-manual-key")
            self.assertEqual(zotero_saved_env(con)["api_key"], "legacy-manual-key")
            self.assertEqual(zotero_env(con)["ZOTERO_API_KEY"], "legacy-manual-key")


if __name__ == "__main__":
    unittest.main()
