import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vitamine.app import citation_network, citation_profile_cited_by, citation_profile_publications, collaboration_map, metrics
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
                      venue, openalex_counts_by_year_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "peer_reviewed", "Visible article", 0, 5.2, 10, "1",
                            "Ada Lovelace, Charles Babbage", "2024", "Brain",
                            '[{"year": 2024, "cited_by_count": 4}]',
                        ),
                        (
                            "preprints", "Visible preprint", 0, 99.0, None, None,
                            "Charles Babbage, Ada Lovelace", "2024", "bioRxiv", "[]",
                        ),
                        (
                            "peer_reviewed", "Hidden duplicate", 1, 30.0, 999, "2",
                            "Ada Lovelace, Charles Babbage", "2024", "Nature",
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
            self.assertNotIn("bioRxiv", [row["venue"] for row in payload["top_venues"]])
            self.assertNotIn("bioRxiv", [row["venue"] for row in payload["impact_factors"]])
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

    def test_citation_explorer_uses_visible_openalex_rows_and_historical_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "citation-explorer.vitamine"
            create_blank_database(path)
            with sqlite3.connect(path) as con:
                con.execute("UPDATE person SET full_name='Ada Lovelace', display_name='Ada Lovelace' WHERE id=1")
                con.executemany(
                    """
                    INSERT INTO publications (
                      category, raw_citation, title, authors, year, openalex_work_id,
                      openalex_cited_by_count, openalex_counts_by_year_json, suppress_display
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "peer_reviewed", "First paper", "First paper", "Ada Lovelace, Charles Babbage", "2020", "https://openalex.org/W1",
                            8, '[{"year": 2023, "cited_by_count": 3}, {"year": 2024, "cited_by_count": 5}]', 0,
                        ),
                        (
                            "peer_reviewed", "Last paper", "Last paper", "Charles Babbage, Ada Lovelace", "2021", "https://openalex.org/W2",
                            5, '[{"year": 2023, "cited_by_count": 2}, {"year": 2024, "cited_by_count": 3}]', 0,
                        ),
                        (
                            "peer_reviewed", "Coauthored paper", "Coauthored paper", "Charles Babbage, Grace Hopper", "2022", "https://openalex.org/W3",
                            1, '[{"year": 2024, "cited_by_count": 1}]', 0,
                        ),
                        (
                            "peer_reviewed", "Hidden paper", "Hidden paper", "Ada Lovelace", "2022", "https://openalex.org/W4",
                            99, '[{"year": 2024, "cited_by_count": 99}]', 1,
                        ),
                    ],
                )
                con.commit()
            with patch("vitamine.app.active_db_path", return_value=path):
                payload = citation_profile_publications()

            self.assertEqual([row["title"] for row in payload["publications"]], ["First paper", "Last paper", "Coauthored paper"])
            self.assertEqual([row["authorship"] for row in payload["publications"]], ["first", "last", "other"])
            self.assertEqual(payload["publications"][0]["researcher_author_indexes"], [0])
            self.assertEqual(payload["publications"][1]["researcher_author_indexes"], [1])
            self.assertEqual(payload["publications"][2]["researcher_author_indexes"], [])
            self.assertEqual(payload["h_index_history"], [
                {"year": 2023, "all": 2, "first_last": 2},
                {"year": 2024, "all": 2, "first_last": 2},
            ])

    def test_citing_works_are_loaded_on_demand_from_openalex(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "citation-cited-by.vitamine"
            create_blank_database(path)
            with sqlite3.connect(path) as con:
                publication_id = con.execute(
                    """INSERT INTO publications (category, raw_citation, title, openalex_work_id, openalex_cited_by_count)
                       VALUES ('peer_reviewed', 'Target work', 'Target work', 'https://openalex.org/W123', 71)"""
                ).lastrowid
                con.commit()
            openalex_payload = {
                "meta": {"count": 71},
                "results": [{
                    "id": "https://openalex.org/W456",
                    "display_name": "A citing study",
                    "publication_year": 2025,
                    "cited_by_count": 8,
                    "doi": "https://doi.org/10.1000/example",
                    "primary_location": {"source": {"display_name": "Example Journal"}},
                    "authorships": [{"author": {"display_name": "Grace Hopper"}}],
                }],
            }
            with patch("vitamine.app.active_db_path", return_value=path), patch(
                "vitamine.app.fetch_json_url", return_value=openalex_payload
            ) as fetch:
                payload = citation_profile_cited_by(publication_id)

            self.assertIn("cites%3AW123", fetch.call_args.args[0])
            self.assertEqual(payload["total"], 71)
            self.assertEqual(payload["next_page"], 2)
            self.assertEqual(payload["works"][0]["title"], "A citing study")
            self.assertEqual(payload["works"][0]["authors"], "Grace Hopper")

    def test_citation_network_returns_cached_citing_work_links(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "citation-network.vitamine"
            create_blank_database(path)
            with sqlite3.connect(path) as con:
                publication_id = con.execute("INSERT INTO publications(category, raw_citation, title, openalex_work_id, openalex_cited_by_count) VALUES ('peer_reviewed', 'Own work', 'Own work', 'https://openalex.org/W1', 8)").lastrowid
                con.execute("INSERT INTO citation_network_works(openalex_work_id, title, cited_by_count) VALUES ('W2', 'Citing work', 4)")
                con.execute("INSERT INTO citation_network_links(source_openalex_work_id, target_publication_id, target_openalex_work_id) VALUES ('W2', ?, 'W1')", (publication_id,))
                con.commit()
            with patch("vitamine.app.active_db_path", return_value=path):
                payload = citation_network()
            self.assertTrue(payload["cached"])
            self.assertEqual(len(payload["links"]), 1)
            self.assertEqual({row["kind"] for row in payload["nodes"]}, {"own", "citing"})
            self.assertIn("last_refreshed_at", payload)

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
        self.assertIn("exploreCitations", document)
        self.assertIn("citationExplorerDialog", document)
        self.assertIn("/static/vendor/cytoscape-3.30.4.min.js", document)
        self.assertIn("citationCitedByLoading", document)
        self.assertIn("citationTitleLink", script)
        self.assertIn("citationDoiHref", script)
        self.assertIn("citationAuthorsMarkup", script)
        self.assertIn("citationNetworkGraph", script)
        self.assertIn("queueCitationNetworkRefresh", script)
        self.assertIn("window.cytoscape", script)
        self.assertIn("runCitationNetworkLayout", script)
        styles = (
            Path(__file__).resolve().parents[1] / "vitamine" / "static" / "styles.css"
        ).read_text(encoding="utf-8")
        self.assertIn("height: 82px;", styles)
        self.assertIn("citationPaperLoader", styles)
        self.assertIn("citationCitedByDialog", styles)
        self.assertIn("citationResearcherAuthor", styles)
        self.assertIn("citationNetworkTooltip", styles)
        self.assertIn("citationNetworkPending", styles)
        self.assertIn("citationNetworkCanvas", styles)
        self.assertIn('citationCountButton[aria-busy="true"]', styles)
        self.assertIn("background: #fff;", styles)
        self.assertIn("min-height: 82px;", styles)


if __name__ == "__main__":
    unittest.main()
