import sqlite3
import unittest
from unittest.mock import patch

from vitamine.enrichment_guard import (
    author_record_matches,
    ensure_discovery_rejections_table,
    guard_publications,
    remembered_rejection,
    review_nonpublications,
    title_similarity,
)


def database() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE person (
          id INTEGER PRIMARY KEY,
          full_name TEXT,
          display_name TEXT,
          degrees TEXT,
          position_title TEXT,
          orcid_id TEXT,
          own_institution_name TEXT
        );
        INSERT INTO person (id, full_name, display_name, degrees, orcid_id, own_institution_name)
        VALUES (
          1, 'Andreas Georg Rudolf Horn', 'Andreas Horn', 'MD, PhD',
          '0000-0002-0000-0001', 'Charité Universitätsmedizin Berlin'
        );
        CREATE TABLE person_identifiers (
          id INTEGER PRIMARY KEY,
          person_id INTEGER,
          platform TEXT,
          identifier_value TEXT,
          url TEXT
        );
        CREATE TABLE publications (
          id INTEGER PRIMARY KEY,
          title TEXT,
          doi TEXT,
          authors TEXT
        );
        INSERT INTO publications (id, title, doi, authors)
        VALUES
          (1, 'Existing DOI paper', '10.1000/existing', 'Andreas Horn, Michael Fox, Simon Eickhoff'),
          (2, 'A DOI-less Paper: Results and Conclusions', '', 'Andreas Horn, Michael Fox, Simon Eickhoff');
        CREATE TABLE cv_entries (
          id INTEGER PRIMARY KEY,
          section_key TEXT,
          start_date TEXT,
          end_date TEXT,
          title TEXT,
          organization TEXT,
          role TEXT,
          amount TEXT,
          description TEXT,
          raw_text TEXT
        );
        INSERT INTO cv_entries
          (id, section_key, start_date, end_date, title, organization, raw_text)
        VALUES
          (1, 'honors', '2020', '2020', 'Example Prize', 'Example Society', 'Example Prize, 2020');
        CREATE TABLE biosketch_contributions (
          id INTEGER PRIMARY KEY,
          ordinal INTEGER,
          title TEXT,
          narrative TEXT
        );
        CREATE TABLE narrative_reports (
          id INTEGER PRIMARY KEY,
          title TEXT,
          body TEXT,
          title_de TEXT,
          body_de TEXT
        );
        """
    )
    ensure_discovery_rejections_table(con)
    return con


class EnrichmentGuardTests(unittest.TestCase):
    def test_title_similarity_normalizes_typography(self):
        self.assertGreaterEqual(
            title_similarity(
                "Parkinson’s disease: a network-based approach",
                "Parkinsons disease - a network based approach",
            ),
            0.90,
        )

    def test_full_conflicting_given_name_is_not_treated_as_matching_initials(self):
        names = ["Andreas Georg Rudolf Horn", "Andreas Horn"]
        self.assertFalse(author_record_matches(names, {"name": "Anja K. E. Horn"}))
        self.assertTrue(author_record_matches(names, {"name": "Andreas Horn"}))
        self.assertTrue(author_record_matches(names, {"name": "Horn A"}))

    @patch("vitamine.enrichment_guard.resolved_publication")
    def test_publication_gate_requires_author_and_removes_duplicates(self, resolve):
        con = database()

        def resolved(payload):
            return (
                {
                    **payload,
                    "doi": payload["resolved_doi"],
                    "authors": payload["resolved_authors"],
                    "raw_citation": payload["title"],
                },
                "",
            )

        resolve.side_effect = resolved
        rows = [
            {
                "title": "Existing DOI paper",
                "resolved_doi": "10.1000/existing",
                "resolved_authors": "Andreas Horn, A. Other",
            },
            {
                "title": "A DOI less paper results and conclusions",
                "resolved_doi": "10.1000/backfilled",
                "resolved_authors": "Andreas Horn, A. Other",
            },
            {
                "title": "Someone else's work",
                "resolved_doi": "10.1000/other",
                "resolved_authors": "Someone Else",
            },
            {
                "title": "Genuinely new work",
                "resolved_doi": "10.1000/new",
                "resolved_authors": "Andreas Horn, A. Other",
            },
        ]
        approved, stats = guard_publications(con, rows, "ai_web_discovery")
        self.assertEqual([row["doi"] for row in approved], ["10.1000/new"])
        self.assertEqual(stats["duplicates"], 1)
        self.assertEqual(stats["backfilled"], 1)
        self.assertEqual(
            con.execute("SELECT doi FROM publications WHERE id=2").fetchone()["doi"],
            "10.1000/backfilled",
        )
        self.assertEqual(stats["not_author"], 1)
        self.assertEqual(stats["approved"], 1)

    @patch("vitamine.enrichment_guard.resolved_publication")
    def test_publication_gate_rejects_homonym_but_accepts_known_affiliation(self, resolve):
        con = database()

        def resolved(payload):
            return (
                {
                    **payload,
                    "doi": payload["doi"],
                    "authors": "Andreas Horn, Unrelated Author",
                    "raw_citation": payload["title"],
                    "_author_records": [
                        {
                            "name": "Andreas Horn",
                            "orcid": "",
                            "affiliations": [payload["affiliation"]],
                            "collective": False,
                        },
                        {
                            "name": "Unrelated Author",
                            "orcid": "",
                            "affiliations": [],
                            "collective": False,
                        },
                    ],
                    "_pubmed_author_records": [
                        {"name": "Horn A", "orcid": "", "affiliations": [], "collective": False}
                    ],
                },
                "",
            )

        resolve.side_effect = resolved
        rows = [
            {
                "title": "Regensburg homonym paper",
                "doi": "10.1000/regensburg",
                "affiliation": "Institute of Biochemistry I, University of Regensburg",
            },
            {
                "title": "Verified Berlin paper",
                "doi": "10.1000/berlin",
                "affiliation": "Charité – Universitätsmedizin Berlin",
            },
        ]
        approved, stats = guard_publications(con, rows, "orcid")
        self.assertEqual([row["doi"] for row in approved], ["10.1000/berlin"])
        self.assertEqual(stats["not_author"], 1)
        self.assertEqual(stats["approved"], 1)

    @patch("vitamine.enrichment_guard.resolved_publication")
    def test_initial_and_shared_coauthors_require_unchecked_identity_review(self, resolve):
        con = database()

        def resolved(payload):
            return (
                {
                    **payload,
                    "authors": "Horn A, Michael Fox, Simon Eickhoff",
                    "raw_citation": payload["title"],
                    "_author_records": [
                        {
                            "name": "Horn A",
                            "orcid": "",
                            "affiliations": payload["affiliations"],
                            "collective": False,
                        },
                        {"name": "Michael Fox", "orcid": "", "affiliations": [], "collective": False},
                        {"name": "Simon Eickhoff", "orcid": "", "affiliations": [], "collective": False},
                    ],
                    "_pubmed_author_records": [
                        {"name": "Horn A", "orcid": "", "affiliations": [], "collective": False}
                    ],
                },
                "",
            )

        resolve.side_effect = resolved
        rows = [
            {
                "title": "Initial-only homonym without affiliation",
                "doi": "10.1000/initial-only",
                "affiliations": [],
            },
            {
                "title": "Initial-only homonym with conflicting affiliation",
                "doi": "10.1000/conflicting-affiliation",
                "affiliations": ["University Medical Center Hamburg-Eppendorf"],
            },
        ]
        approved, stats = guard_publications(con, rows, "orcid")
        self.assertEqual(len(approved), 2)
        self.assertTrue(all(row["confidence"] == "low" for row in approved))
        self.assertTrue(all(row["_identity_review_required"] for row in approved))
        self.assertEqual(stats["identity_review"], 2)
        self.assertEqual(stats["not_author"], 0)

    @patch("vitamine.enrichment_guard.llm_json")
    def test_nonpublication_review_is_llm_only_and_remembers_rejection(self, judge):
        con = database()
        entries = [
            {
                "section_key": "honors",
                "title": "Example Prize",
                "start_date": "2020",
                "end_date": "2020",
                "organization": "Example Society",
                "raw_text": "Example Prize, 2020",
            },
            {
                "section_key": "honors",
                "title": "New Prize",
                "start_date": "2026",
                "end_date": "2026",
                "organization": "New Society",
                "raw_text": "New Prize, 2026",
            },
        ]
        judge.return_value = (
            {
                "decisions": [
                    {
                        "candidate_id": "entry:0",
                        "understood": True,
                        "complete": True,
                        "certainly_novel": False,
                        "approve": False,
                        "reason": "Already present.",
                    },
                    {
                        "candidate_id": "entry:1",
                        "understood": True,
                        "complete": True,
                        "certainly_novel": True,
                        "approve": True,
                        "reason": "Complete and new.",
                    },
                ]
            },
            None,
        )
        approved, stats = review_nonpublications(con, entries, [], {}, None, "ai_web_discovery", {"provider": "test"})
        self.assertEqual([row["title"] for row in approved["entries"]], ["New Prize"])
        self.assertEqual(stats["rejected"], 1)
        self.assertTrue(remembered_rejection(con, "entry", entries[0]))


if __name__ == "__main__":
    unittest.main()
