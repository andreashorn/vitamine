import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from vitamine.cloud_app import create_blank_workspace_database
from vitamine.public_profiles import build_public_profile_snapshot


class PublicProfileSnapshotTests(unittest.TestCase):
    def test_snapshot_whitelists_public_data_and_builds_the_four_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "profile.vitamine"
            create_blank_workspace_database(database)
            with sqlite3.connect(database) as con:
                con.execute(
                    """
                    UPDATE person
                    SET full_name='Ada Private',
                        display_name='Ada Public',
                        degrees='PhD',
                        position_title='Professor',
                        office_address='Secret office',
                        home_address='Secret home',
                        work_phone='+49 0000',
                        work_email='private@example.org',
                        place_of_birth='Private birthplace',
                        orcid_id='0000-0002-1825-0097',
                        own_institution_name='Example University',
                        own_institution_country='Germany',
                        own_institution_country_code='DE',
                        own_institution_latitude=50.94,
                        own_institution_longitude=6.96
                    WHERE id=1
                    """
                )
                con.execute(
                    """
                    INSERT INTO narrative_reports(id, title, body)
                    VALUES (1, 'About', 'Studies transparent research software.')
                    ON CONFLICT(id) DO UPDATE SET body=excluded.body
                    """
                )
                publication = con.execute(
                    """
                    INSERT INTO publications(
                      source, category, authors, title, venue, year, doi, raw_citation,
                      impact_factor, openalex_cited_by_count, openalex_counts_by_year_json
                    )
                    VALUES (
                      'manual', 'peer_reviewed', 'Ada Public, Grace Example',
                      'A public paper', 'Example Journal', '2024', '10.1000/example',
                      'Citation', 5.2, 12,
                      '[{"year": 2024, "cited_by_count": 4}]'
                    )
                    """
                ).lastrowid
                con.execute(
                    """
                    INSERT INTO collaboration_institutions(
                      publication_id, institution_id, institution_name, country_code,
                      country, latitude, longitude, author_name, publication_year
                    )
                    VALUES (?, 'I123', 'Collaborator University', 'US',
                            'United States', 42.36, -71.06, 'Grace Example', '2024')
                    """,
                    (publication,),
                )
            snapshot = build_public_profile_snapshot(
                database,
                database_id="database-123",
            )

        serialized = json.dumps(snapshot)
        self.assertEqual(snapshot["schema_version"], 2)
        self.assertEqual(snapshot["display_name"], "Ada Public")
        self.assertEqual(snapshot["profile_title"], "Ada Public, PhD")
        self.assertEqual(snapshot["bio"]["institution"], "Example University")
        self.assertEqual(snapshot["metrics"]["total_publications"], 1)
        self.assertEqual(snapshot["metrics"]["total_citations"], 12)
        self.assertEqual(snapshot["metrics"]["all"]["h_index"], 1)
        self.assertEqual(snapshot["collaborators"]["institution_count"], 1)
        self.assertEqual(
            [block["key"] for block in snapshot["blocks"]],
            ["bio", "metrics", "publications", "collaborators"],
        )
        self.assertNotIn("Secret office", serialized)
        self.assertNotIn("Secret home", serialized)
        self.assertNotIn("private@example.org", serialized)
        self.assertNotIn("+49 0000", serialized)
        self.assertNotIn("Private birthplace", serialized)


if __name__ == "__main__":
    unittest.main()
