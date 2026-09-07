import unittest
from pathlib import Path


class PlusWorkspaceToggleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        static_root = Path(__file__).resolve().parents[1] / "vitamine" / "static"
        cls.script = (static_root / "app.js").read_text(encoding="utf-8")
        cls.page = (static_root / "index.html").read_text(encoding="utf-8")
        cls.styles = (static_root / "styles.css").read_text(encoding="utf-8")

    def test_developer_toggle_is_prominent_and_account_scoped(self):
        self.assertIn("DEV · Plus ON", self.script)
        self.assertIn("DEV · Free mode", self.script)
        self.assertIn("state.cloud.plus?.developer_toggle", self.script)
        self.assertIn('api("/api/account/plus-developer-toggle"', self.script)
        self.assertIn("workspacePlusButton.developer", self.styles)
        self.assertIn('id="workspacePlusButton"', self.page)


if __name__ == "__main__":
    unittest.main()
