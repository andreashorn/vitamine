import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "strato" / "mailbox-maintenance.py"
SPEC = importlib.util.spec_from_file_location("vitamine_mailbox_maintenance", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class FakeClient:
    def __init__(self):
        self.stored = []
        self.expunge_count = 0

    def list(self):
        return "OK", [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren \\Sent) "/" "Sent"',
            b'(\\HasNoChildren \\Trash) "/" "Deleted Messages"',
            b'(\\HasNoChildren \\Junk) "/" "Spam"',
        ]

    def select(self, folder):
        return "OK", [b"2"]

    def search(self, charset, criterion, before):
        return "OK", [b"4 9"]

    def store(self, message_id, operation, flags):
        self.stored.append((message_id, operation, flags))
        return "OK", []

    def expunge(self):
        self.expunge_count += 1
        return "OK", []

    def getquotaroot(self, mailbox):
        return "OK", [[b'INBOX ""'], [b'"" (STORAGE 12 5242880 MAILBOX 6 5000)']]


class MailboxMaintenanceTests(unittest.TestCase):
    def test_only_special_use_trash_and_junk_are_selected(self):
        self.assertEqual(
            MODULE.special_use_folders(FakeClient()),
            ["Deleted Messages", "Spam"],
        )

    def test_expiry_marks_only_search_results_and_expunge(self):
        client = FakeClient()
        deleted = MODULE.expire_folder(client, "Spam", MODULE.dt.date(2026, 7, 1))
        self.assertEqual(deleted, 2)
        self.assertEqual([row[0] for row in client.stored], [b"4", b"9"])
        self.assertEqual(client.expunge_count, 1)

    def test_nested_strato_quota_response_is_parsed(self):
        self.assertEqual(MODULE.quota_usage(FakeClient()), (12, 5_242_880))


if __name__ == "__main__":
    unittest.main()
