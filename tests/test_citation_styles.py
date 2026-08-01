import unittest
from pathlib import Path

from vitamine.citation_styles import (
    CITATION_STYLES,
    format_publication,
    publication_to_csl,
    validate_citation_style,
)


class CitationStyleTests(unittest.TestCase):
    def setUp(self):
        self.publication = {
            "id": 1,
            "authors": "Nanditha Rajamani, Rob M. A. de Bie, Andreas Horn",
            "title": "A scientific result",
            "venue": "Nature Communications",
            "year": "2024",
            "doi": "10.1234/example",
            "url": "https://doi.org/10.1234/example",
            "volume": "15",
            "issue": "2",
            "pages": "1-9",
        }

    def test_default_style_preserves_existing_exporter(self):
        self.assertIsNone(format_publication(self.publication, "vitamine-long"))
        self.assertEqual(validate_citation_style("unknown"), "vitamine-long")
        default = next(style for style in CITATION_STYLES if style["id"] == "vitamine-long")
        self.assertEqual(default["label"], "VitaMine Default")

    def test_csl_metadata_preserves_surname_particles_and_avoids_duplicate_doi_url(self):
        item = publication_to_csl(self.publication)
        self.assertEqual(item["author"][1]["family"], "de Bie")
        self.assertEqual(item["DOI"], "10.1234/example")
        self.assertNotIn("URL", item)

    def test_curated_styles_render_real_bibliography_text(self):
        available = {style["id"] for style in CITATION_STYLES}
        self.assertEqual(
            available,
            {
                "vitamine-long",
                "apa",
                "harvard",
                "chicago-author-date",
                "mla",
                "dgps",
                "vancouver",
                "nlm",
                "ama",
                "jama",
                "ieee",
                "nature",
                "science",
                "cell",
                "lancet",
                "nejm",
                "bmj",
                "plos",
                "elife",
                "frontiers",
                "acs",
                "rsc",
                "springer-author-date",
                "elsevier-harvard",
            },
        )
        self.assertTrue(all(style.get("group") for style in CITATION_STYLES))
        for style in available - {"vitamine-long"}:
            rendered = format_publication(self.publication, style)
            self.assertIn("a scientific result", rendered.casefold())
            self.assertNotRegex(rendered, r"^(?:1\.|\[1\]|\(1\))")
            self.assertLessEqual(rendered.count("10.1234/example"), 1)
        self.assertEqual(format_publication(self.publication, "vancouver").count("10.1234/example"), 1)
        self.assertEqual(format_publication(self.publication, "apa").count("10.1234/example"), 1)

    def test_dropdown_groups_the_curated_styles(self):
        script = (
            Path(__file__).resolve().parents[1]
            / "vitamine"
            / "static"
            / "app.js"
        ).read_text(encoding="utf-8")
        self.assertIn("<optgroup", script)
        self.assertIn("option.group", script)


if __name__ == "__main__":
    unittest.main()
