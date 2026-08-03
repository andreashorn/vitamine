import json
import unittest
from pathlib import Path

from vitamine.app import EXPORT_CONTENT_PROFILES, export_format_catalog


ROOT = Path(__file__).resolve().parents[1]


class ExportFormatCatalogTests(unittest.TestCase):
    def test_every_style_has_exactly_one_supported_content_profile(self):
        formats = export_format_catalog()

        self.assertTrue(formats)
        self.assertEqual(
            {item["content_profile"] for item in formats},
            set(EXPORT_CONTENT_PROFILES),
        )
        for item in formats:
            self.assertIn(item["content_profile"], EXPORT_CONTENT_PROFILES)
            self.assertEqual(
                item["content_profile_label"],
                EXPORT_CONTENT_PROFILES[item["content_profile"]]["label"],
            )

    def test_catalog_schema_records_content_profile_separately_from_renderer(self):
        payload = json.loads(
            (ROOT / "vitamine" / "static" / "export-formats.json").read_text(encoding="utf-8")
        )

        self.assertEqual(payload["schema_version"], 2)
        preview_only = [item for item in payload["formats"] if item["exporter"] is None]
        self.assertTrue(preview_only)
        self.assertTrue(all(item["content_profile"] for item in preview_only))

    def test_library_renders_the_content_profile_label(self):
        script = (ROOT / "vitamine" / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('class="contentProfileBadge"', script)
        self.assertIn("format.content_profile_label", script)
        self.assertIn('format.content_profile === "long"', script)

    def test_working_formats_use_green_icons_and_dfg_is_exportable(self):
        formats = export_format_catalog()
        preinstalled = [item for item in formats if item.get("preinstalled")]
        self.assertEqual(len(preinstalled), 4)
        self.assertTrue(all(item["quality"]["key"] == "ready" for item in preinstalled))
        dfg = next(item for item in formats if item["id"] == "vitamine.dfg-research-cv")
        self.assertEqual(dfg["exporter"], "bundled_docx")
        self.assertEqual(dfg["languages"], ["en"])
        self.assertEqual(dfg["page_limit"], 4)

        script = (ROOT / "vitamine" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("qualityReadyIcon", script)
        self.assertIn("Fixed limit:", script)

    def test_export_buttons_use_format_focused_wording(self):
        script = (ROOT / "vitamine" / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("Export CV in this format", script)
        self.assertNotIn("Export Word document", script)


if __name__ == "__main__":
    unittest.main()
