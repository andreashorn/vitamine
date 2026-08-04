import asyncio
import unittest

from vitamine.cloud_app import (
    zotero_item_payload,
    zotero_patch_source_membership,
    zotero_source_write_access,
)


class _Response:
    status_code = 204

    def __init__(self, payload=None):
        self.payload = payload or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Client:
    def __init__(self):
        self.patches = []

    async def get(self, _url, **_kwargs):
        return _Response({"data": {"version": 8, "collections": ["KEEP", "NETSTIM"], "DOI": "10.1000/x"}})

    async def patch(self, url, **kwargs):
        self.patches.append((url, kwargs))
        return _Response()


class ZoteroProfileSyncGatewayTests(unittest.TestCase):
    def test_write_access_is_checked_for_the_exact_group(self):
        access = {
            "access": {
                "user": {"library": True, "write": True},
                "groups": {"42": {"library": True, "write": False}, "99": {"library": True, "write": True}},
            }
        }
        self.assertFalse(zotero_source_write_access(access, {"library_type": "groups", "library_id": "42"}))
        self.assertTrue(zotero_source_write_access(access, {"library_type": "groups", "library_id": "99"}))
        self.assertTrue(zotero_source_write_access(access, {"library_type": "users", "library_id": "7"}))

    def test_collection_removal_preserves_the_library_item_and_other_collections(self):
        client = _Client()
        asyncio.run(
            zotero_patch_source_membership(
                client,
                prefix="https://api.zotero.org/groups/42",
                api_key="not-a-real-key",
                source={"source_mode": "collection", "collection_key": "NETSTIM"},
                remote_id="ABCD1234",
                present=False,
            )
        )
        self.assertEqual(len(client.patches), 1)
        url, request = client.patches[0]
        self.assertTrue(url.endswith("/items/ABCD1234"))
        self.assertEqual(request["json"], {"collections": ["KEEP"]})
        self.assertEqual(request["headers"]["If-Unmodified-Since-Version"], "8")

    def test_new_collection_item_is_created_in_only_the_selected_collection(self):
        payload = zotero_item_payload(
            {"title": "A paper", "doi": "10.1000/x", "authors": "A. Author", "venue": "Journal", "year": "2025"},
            {"source_mode": "collection", "collection_key": "NETSTIM"},
        )
        self.assertEqual(payload["collections"], ["NETSTIM"])
        self.assertEqual(payload["DOI"], "10.1000/x")


if __name__ == "__main__":
    unittest.main()
