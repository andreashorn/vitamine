import unittest
import sys
from pathlib import Path
from unittest.mock import patch

from vitamine.app import zotero_accessible_libraries, zotero_status

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vitamine" / "scripts"))
from vitamine.scripts.sync_zotero import accessible_libraries as sync_accessible_libraries


class ZoteroLibraryTests(unittest.TestCase):
    def test_all_group_access_expands_to_concrete_group_ids(self):
        key_info = {
            "userID": 123,
            "displayName": "Andreas Horn",
            "access": {
                "user": {"library": True},
                "groups": {"all": {"library": True, "write": False}},
            },
        }
        groups = {
            "456": "Netstim Publications",
            "789": "Another Group",
            "1011": "Third Group",
        }
        with (
            patch("vitamine.app.zotero_key_info", return_value=key_info),
            patch("vitamine.app.zotero_group_names", return_value=groups),
        ):
            _info, libraries = zotero_accessible_libraries("secret")

        self.assertEqual(
            [(row["type"], row["id"], row["name"]) for row in libraries],
            [
                ("users", "123", "Andreas Horn library"),
                ("groups", "456", "Netstim Publications"),
                ("groups", "789", "Another Group"),
                ("groups", "1011", "Third Group"),
            ],
        )
        self.assertNotIn("all", {row["id"] for row in libraries})

    def test_sync_worker_also_expands_all_group_access(self):
        key_info = {
            "userID": 123,
            "displayName": "Andreas Horn",
            "access": {
                "user": {"library": True},
                "groups": {"all": {"library": True, "write": False}},
            },
        }
        group_rows = [
            {"data": {"id": 456, "name": "Netstim Publications"}},
            {"data": {"id": 789, "name": "Another Group"}},
        ]
        with patch(
            "vitamine.scripts.sync_zotero.zotero_request",
            side_effect=[(key_info, {}), (group_rows, {})],
        ):
            libraries = sync_accessible_libraries("secret")

        self.assertEqual(
            [(row["type"], row["id"], row["name"]) for row in libraries],
            [
                ("users", "123", "Andreas Horn"),
                ("groups", "456", "Netstim Publications"),
                ("groups", "789", "Another Group"),
            ],
        )

    def test_status_endpoint_does_not_overwrite_saved_library_choice(self):
        saved_env = {
            "api_key": "secret",
            "library_type": "groups",
            "library_id": "456",
            "group_name": "Netstim Publications",
            "collection_key": "",
            "source_mode": "library",
            "collection_name": "",
        }
        libraries = [
            {"type": "users", "id": "123", "name": "Personal library", "kind": "Personal library"},
            {"type": "groups", "id": "456", "name": "Netstim Publications", "kind": "Group library"},
            {"type": "groups", "id": "789", "name": "Lead-DBS Publications", "kind": "Group library"},
        ]
        connection = unittest.mock.MagicMock()
        connection.__enter__.return_value = connection
        with (
            patch("vitamine.app.connect", return_value=connection),
            patch("vitamine.app.zotero_saved_env", return_value=saved_env),
            patch("vitamine.app.zotero_accessible_libraries", return_value=({}, libraries)),
            patch("vitamine.app.zotero_fetch_collections", return_value=[]),
            patch("vitamine.app.set_setting") as set_setting,
        ):
            result = zotero_status()

        self.assertTrue(result["ok"])
        self.assertEqual(result["library"]["id"], "456")
        self.assertEqual(result["library"]["name"], "Netstim Publications")
        self.assertEqual([row["id"] for row in result["libraries"]], ["123", "456", "789"])
        set_setting.assert_not_called()


if __name__ == "__main__":
    unittest.main()
