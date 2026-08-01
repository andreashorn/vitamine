import unittest
from pathlib import Path


class CloudExportDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = (
            Path(__file__).resolve().parents[1] / "vitamine" / "static" / "app.js"
        ).read_text(encoding="utf-8")

    def test_cloud_cards_do_not_show_last_export_link(self):
        self.assertIn("artifact.docx && !state.cloud.enabled", self.script)

    def test_cloud_export_uses_download_attribute_instead_of_new_window(self):
        self.assertIn("if (state.cloud.enabled) {", self.script)
        self.assertIn('document.createElement("a")', self.script)
        self.assertIn("download.download =", self.script)
        self.assertIn("download.click()", self.script)
        self.assertIn('window.open(href, "_blank"', self.script)


if __name__ == "__main__":
    unittest.main()
