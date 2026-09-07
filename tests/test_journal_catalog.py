import sqlite3
import unittest

from vitamine.journal_catalog import (
    apply_journal_canonical_title,
    ensure_journal_catalog_tables,
    journal_catalog_preview,
)


class JournalCatalogTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(
            """
            CREATE TABLE publications (id INTEGER PRIMARY KEY, venue TEXT);
            INSERT INTO publications (id, venue) VALUES
              (1, 'BRAIN'),
              (2, 'BRAIN'),
              (3, 'Brain: A Journal of Neurology');
            """
        )
        ensure_journal_catalog_tables(self.con)

    def tearDown(self):
        self.con.close()

    def test_catalog_applies_a_user_chosen_canonical_title_to_known_aliases(self):
        first = apply_journal_canonical_title(self.con, 1, "Brain")
        self.assertEqual(first["matched"], 2)
        self.assertEqual([row["id"] for row in first["updated"]], [1, 2])
        self.assertEqual(
            [row[0] for row in self.con.execute("SELECT venue FROM publications ORDER BY id")],
            ["Brain", "Brain", "Brain: A Journal of Neurology"],
        )

        preview = journal_catalog_preview(self.con, 1, "Brain: A Journal of Neurology")
        self.assertEqual(preview["matches"], 2)
        second = apply_journal_canonical_title(self.con, 1, "Brain: A Journal of Neurology")
        self.assertEqual([row["id"] for row in second["updated"]], [1, 2])
        self.assertEqual(
            [row[0] for row in self.con.execute("SELECT venue FROM publications ORDER BY id")],
            ["Brain: A Journal of Neurology"] * 3,
        )


if __name__ == "__main__":
    unittest.main()
