import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vitamine.cloud_app import (
    CLOUD_SCHEMA_VERSION,
    connect,
    initialize_database,
    run_cloud_migrations,
)


class CloudMigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "cloud.sqlite"
        self.environment = patch.dict(
            os.environ,
            {
                "VITAMINE_CLOUD_DB": str(self.database),
                "VITAMINE_DATABASE_URL": "",
            },
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.directory.cleanup()

    def schema_version(self) -> int:
        with sqlite3.connect(self.database) as con:
            return int(
                con.execute(
                    "SELECT version FROM cloud_schema_metadata WHERE singleton=1"
                ).fetchone()[0]
            )

    def table_columns(self, table: str) -> set[str]:
        with sqlite3.connect(self.database) as con:
            return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}

    def seed_version(self, version: int) -> None:
        with sqlite3.connect(self.database) as con:
            con.execute(
                """
                CREATE TABLE cloud_schema_metadata (
                    singleton INTEGER PRIMARY KEY,
                    version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            con.execute(
                "INSERT INTO cloud_schema_metadata VALUES (1, ?, 'historical')",
                (version,),
            )
            con.commit()

    def test_fresh_store_uses_migrations(self):
        initialize_database()
        self.assertEqual(self.schema_version(), CLOUD_SCHEMA_VERSION)
        self.assertIn("portrait_blob", self.table_columns("public_profiles"))
        with sqlite3.connect(self.database) as con:
            tables = {
                row[0]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertIn("background_jobs", tables)
        self.assertIn("orcid_oauth_connections", tables)

    def test_version_one_store_upgrades_without_losing_member_data(self):
        self.seed_version(1)
        with sqlite3.connect(self.database) as con:
            con.execute(
                """
                CREATE TABLE members (
                    id TEXT PRIMARY KEY,
                    invitation_id INTEGER,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    revoked_at TEXT
                )
                """
            )
            con.execute(
                """
                INSERT INTO members(id, invitation_id, created_at, last_seen_at)
                VALUES ('member-1', 1, 'then', 'then')
                """
            )
            con.execute(
                """
                CREATE TABLE workspace_sessions (
                    id TEXT PRIMARY KEY,
                    member_id TEXT,
                    token_hash TEXT,
                    db_path TEXT,
                    output_path TEXT,
                    port INTEGER,
                    original_filename TEXT,
                    created_at TEXT,
                    last_seen_at TEXT,
                    expires_at TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE public_profiles (
                    slug TEXT PRIMARY KEY,
                    member_id TEXT,
                    snapshot_json TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    published_at TEXT
                )
                """
            )
            con.commit()
        initialize_database()
        self.assertEqual(self.schema_version(), CLOUD_SCHEMA_VERSION)
        self.assertIn("email", self.table_columns("members"))
        self.assertIn("database_id", self.table_columns("workspace_sessions"))
        with sqlite3.connect(self.database) as con:
            row = con.execute(
                "SELECT id, created_at FROM members WHERE id='member-1'"
            ).fetchone()
        self.assertEqual(row, ("member-1", "then"))

    def test_version_two_store_receives_oauth_and_portrait_schema(self):
        self.seed_version(2)
        with sqlite3.connect(self.database) as con:
            con.execute(
                """
                CREATE TABLE public_profiles (
                    slug TEXT PRIMARY KEY,
                    member_id TEXT,
                    snapshot_json TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    published_at TEXT
                )
                """
            )
            con.commit()
        initialize_database()
        self.assertEqual(self.schema_version(), CLOUD_SCHEMA_VERSION)
        self.assertIn("portrait_blob", self.table_columns("public_profiles"))
        with sqlite3.connect(self.database) as con:
            exists = con.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='oauth_authorization_states'
                """
            ).fetchone()
        self.assertIsNotNone(exists)

    def test_version_three_store_receives_background_job_idempotency_schema(self):
        self.seed_version(3)
        with sqlite3.connect(self.database) as con:
            con.execute(
                """
                CREATE TABLE background_jobs (
                    id TEXT PRIMARY KEY,
                    member_id TEXT NOT NULL,
                    database_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            con.execute(
                """
                INSERT INTO background_jobs(id, member_id, database_id, kind)
                VALUES ('job-1', 'member-1', 'database-1', 'enrich_cv')
                """
            )
            con.commit()
        initialize_database()
        self.assertEqual(self.schema_version(), CLOUD_SCHEMA_VERSION)
        self.assertIn("idempotency_key", self.table_columns("background_jobs"))
        self.assertIn("request_fingerprint", self.table_columns("background_jobs"))
        with sqlite3.connect(self.database) as con:
            existing = con.execute(
                "SELECT id, kind FROM background_jobs WHERE id='job-1'"
            ).fetchone()
            index = con.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type='index' AND name='idx_background_jobs_idempotency'
                """
            ).fetchone()
        self.assertEqual(existing, ("job-1", "enrich_cv"))
        self.assertIsNotNone(index)

    def test_repeated_migration_is_a_noop(self):
        initialize_database()
        with connect() as con:
            self.assertEqual(run_cloud_migrations(con), [])
        self.assertEqual(self.schema_version(), CLOUD_SCHEMA_VERSION)

    def test_future_schema_is_rejected_without_rewriting_version(self):
        future_version = CLOUD_SCHEMA_VERSION + 1
        self.seed_version(future_version)
        with self.assertRaisesRegex(RuntimeError, "newer"):
            initialize_database()
        self.assertEqual(self.schema_version(), future_version)

    def test_failed_migration_rolls_back_its_schema_and_version(self):
        def failing_migration(con):
            con.execute("CREATE TABLE should_be_rolled_back (id INTEGER)")
            raise RuntimeError("planned test failure")

        with patch(
            "vitamine.cloud_app.CLOUD_MIGRATIONS",
            ((1, failing_migration),),
        ):
            with self.assertRaisesRegex(RuntimeError, "planned test failure"):
                initialize_database()
        with sqlite3.connect(self.database) as con:
            partial = con.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='should_be_rolled_back'
                """
            ).fetchone()
        self.assertIsNone(partial)


@unittest.skipUnless(
    os.environ.get("VITAMINE_TEST_POSTGRES_URL"),
    "Set VITAMINE_TEST_POSTGRES_URL to a disposable PostgreSQL database.",
)
class PostgreSQLCloudMigrationTests(unittest.TestCase):
    def test_disposable_postgres_reaches_current_schema(self):
        with patch.dict(
            os.environ,
            {"VITAMINE_DATABASE_URL": os.environ["VITAMINE_TEST_POSTGRES_URL"]},
        ):
            initialize_database()
            with connect() as con:
                row = con.execute(
                    "SELECT version FROM cloud_schema_metadata WHERE singleton=1"
                ).fetchone()
        self.assertEqual(int(row["version"]), CLOUD_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
