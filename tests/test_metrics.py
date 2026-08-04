import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vitamine.app import collaboration_map, metrics
from vitamine.paths import create_blank_database


class MetricsTests(unittest.TestCase):
    def test_dashboard_citation_map_ranks_citing_researchers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "citation-map.vitamine"
            create_blank_database(path)
            with sqlite3.connect(path) as con:
                con.execute(
                    """UPDATE person SET full_name='Ada Lovelace', display_name='Ada Lovelace',
                       own_institution_name='Own University', own_institution_country='UK',
                       own_institution_latitude=51.5, own_institution_longitude=-0.1 WHERE id=1"""
                )
                publication_id = con.execute(
                    "INSERT INTO publications(category, raw_citation) VALUES ('peer_reviewed', 'Paper')"
                ).lastrowid
                con.executemany(
                    """INSERT INTO citation_institutions(
                       publication_id, citing_openalex_work_id, author_id, author_name,
                       institution_id, institution_name, country, latitude, longitude
                       ) VALUES (?, ?, 'A1', 'Grace Citer', 'I1', 'Citing University',
                                 'France', 48.86, 2.35)""",
                    [(publication_id, "W1"), (publication_id, "W2")],
                )
            with patch("vitamine.app.active_db_path", return_value=path):
                payload = collaboration_map("citations")
            self.assertEqual(payload["mode"], "citations")
            self.assertEqual(payload["researcher_count"], 1)
            self.assertEqual(payload["citation_links"], 2)
            self.assertEqual(payload["nodes"][1]["researchers"][0]["name"], "Grace Citer")

    def test_profile_metrics_exclude_hidden_problem_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.vitamine"
            create_blank_database(path)
            with sqlite3.connect(path) as con:
                con.execute(
                    """
                    UPDATE person
                    SET full_name='Ada Lovelace', display_name='Ada Lovelace'
                    WHERE id=1
                    """
                )
                con.executemany(
                    """
                    INSERT INTO publications (
                      category, raw_citation, suppress_display, impact_factor,
                      openalex_cited_by_count, orcid_put_code, authors, year,
                      openalex_counts_by_year_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "peer_reviewed", "Visible article", 0, 5.2, 10, "1",
                            "Ada Lovelace, Charles Babbage", "2024",
                            '[{"year": 2024, "cited_by_count": 4}]',
                        ),
                        (
                            "preprints", "Visible preprint", 0, None, None, None,
                            "Charles Babbage, Ada Lovelace", "2024", "[]",
                        ),
                        (
                            "peer_reviewed", "Hidden duplicate", 1, 30.0, 999, "2",
                            "Ada Lovelace, Charles Babbage", "2024",
                            '[{"year": 2024, "cited_by_count": 999}]',
                        ),
                    ],
                )
                con.commit()

            with patch("vitamine.app.active_db_path", return_value=path):
                payload = metrics()

            publications = payload["publications"]
            self.assertEqual(publications["visible"], 2)
            self.assertEqual(publications["peer_reviewed"], 1)
            self.assertEqual(publications["citation_metric_count"], 1)
            self.assertEqual(publications["openalex_cited_by_total"], 10)
            self.assertEqual(publications["impact_factor_count"], 1)
            self.assertEqual(publications["orcid_matched"], 1)
            self.assertEqual(publications["suppressed"], 1)
            year_2024 = next(
                row for row in payload["citation_profile"]["by_year"]
                if row["year"] == "2024"
            )
            self.assertEqual(year_2024["citations"], 4)
            self.assertEqual(year_2024["first_last_author_citations"], 4)
            self.assertEqual(year_2024["publications_published"], 2)
            self.assertEqual(year_2024["impact_factor_sum"], 5.2)
            self.assertEqual(year_2024["impact_factor_count"], 1)
            first_last = payload["citation_profile"]["first_last_author"]
            self.assertEqual(first_last["citations"], 10)
            self.assertEqual(first_last["h_index"], 1)
            self.assertEqual(first_last["i10_index"], 1)
            first_last_recent = payload["citation_profile"][
                "first_last_author_since_yearly_citations"
            ]
            self.assertEqual(first_last_recent["citations"], 4)
            self.assertEqual(first_last_recent["h_index"], 1)
            self.assertEqual(first_last_recent["i10_index"], 0)

    def test_dashboard_uses_public_facing_metric_labels(self):
        script = (
            Path(__file__).resolve().parents[1] / "vitamine" / "static" / "app.js"
        ).read_text(encoding="utf-8")
        self.assertIn('"Total Publications"', script)
        self.assertIn('"Peer Reviewed Publications"', script)
        self.assertIn('"Total Citations"', script)
        self.assertIn('"Publications with Citation Data"', script)
        self.assertNotIn('metricCard("Short selected"', script)
        self.assertNotIn('metricCard("Ultrashort"', script)
        self.assertNotIn('metricCard("Missing year"', script)
        self.assertNotIn('metricCard("Missing DOI"', script)
        self.assertIn("Combined journal Impact Factors", script)
        self.assertIn("First/last-author citations", script)
        self.assertIn("citationYearDetail", script)
        self.assertIn("first_last_author_since_yearly_citations", script)
        self.assertIn("All Publications", script)
        self.assertIn("First/Last-author publications", script)
        self.assertIn("citationYearDetail isEmpty", script)
        self.assertIn("new Date().getFullYear()", script)
        self.assertIn('item.addEventListener("click"', script)
        self.assertNotIn('item.addEventListener("mouseenter"', script)
        document = (
            Path(__file__).resolve().parents[1] / "vitamine" / "static" / "index.html"
        ).read_text(encoding="utf-8")
        self.assertNotIn("citation data refreshes automatically", document)
        self.assertIn("20260804-cleanup-side-by-side-preview", document)
        styles = (
            Path(__file__).resolve().parents[1] / "vitamine" / "static" / "styles.css"
        ).read_text(encoding="utf-8")
        self.assertIn("height: 82px;", styles)
        self.assertIn("min-height: 82px;", styles)


if __name__ == "__main__":
    unittest.main()
