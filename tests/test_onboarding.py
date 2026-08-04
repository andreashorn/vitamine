import sqlite3
import tempfile
import unittest
from pathlib import Path

from vitamine.app import onboarding_payload
from vitamine.paths import create_blank_database


class OnboardingTests(unittest.TestCase):
    def test_hosted_orcid_dialog_explains_unconfigured_oauth(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "vitamine" / "static" / "index.html").read_text()
        script = (root / "vitamine" / "static" / "app.js").read_text()

        self.assertIn("20260804-cleanup-crossref-verification", html)
        self.assertEqual(html.count('id="enrichCvDashboard"'), 1)
        self.assertLess(html.index('id="enrichCvDashboard"'), html.index('id="cloudAccountMenu"'))
        self.assertIn('class="topbarEnrichButton"', html)
        self.assertIn('id="orcidOauthDescription"', html)
        self.assertIn('id="linkOrcid" class="orcidLinkButton"', html)
        self.assertGreaterEqual(html.count('class="orcidIdMark"'), 2)
        self.assertIn('id="orcidConnectionTitle">ORCID', html)
        self.assertIn('id="zoteroConnectionTitle">Zotero', html)
        self.assertIn('class="zoteroMark"', html)
        self.assertIn('id="testZoteroConnection"', html)
        self.assertIn('>Refresh libraries</button>', html)
        self.assertNotIn('>Test Link</button>', html)
        self.assertIn(
            "Secure ORCID sign-in has not yet been enabled for this VitaMine deployment.",
            script,
        )
        self.assertIn("panel.hidden = !state.cloud.enabled", script)

    def test_blank_database_starts_on_cv_import_step(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "default.vitamine"
            create_blank_database(path)
            con = sqlite3.connect(path)
            con.row_factory = sqlite3.Row
            try:
                payload = onboarding_payload(con)
                self.assertTrue(payload["enabled"])
                self.assertEqual(payload["step"], "import_cv")
                self.assertTrue(payload["population"]["is_empty"])
            finally:
                con.close()

    def test_existing_populated_database_does_not_start_tour_implicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.vitamine"
            create_blank_database(path)
            con = sqlite3.connect(path)
            con.row_factory = sqlite3.Row
            try:
                con.execute("DELETE FROM app_settings WHERE key='onboarding_enabled'")
                con.execute("UPDATE person SET full_name='Existing User' WHERE id=1")
                con.commit()
                payload = onboarding_payload(con)
                self.assertFalse(payload["enabled"])
                self.assertEqual(payload["step"], "")
            finally:
                con.close()


if __name__ == "__main__":
    unittest.main()
