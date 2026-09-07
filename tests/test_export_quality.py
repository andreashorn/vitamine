import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from vitamine.export_quality import (
    apply_corrections,
    conservative_cleanup,
    extract_docx_text,
    is_safe_automatic_change,
    run_export_quality_audit,
)


class ExportQualityTests(unittest.TestCase):
    def connection(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.execute("CREATE TABLE person (id INTEGER PRIMARY KEY, full_name TEXT, position_title TEXT)")
        con.execute("CREATE TABLE cv_entries (id INTEGER PRIMARY KEY, title TEXT, description TEXT)")
        con.execute("CREATE TABLE publications (id INTEGER PRIMARY KEY, title TEXT, raw_citation TEXT)")
        return con

    def test_cleanup_is_meaning_preserving(self):
        self.assertEqual(conservative_cleanup("  Research\u00a0 &amp;  Teaching  "), "Research & Teaching")
        self.assertEqual(conservative_cleanup("DirectorÄapos;s  Office"), "Director's Office")

    def test_semantic_change_cannot_be_applied(self):
        self.assertFalse(is_safe_automatic_change("Assistant Professor", "Professor", "possible_factual_error"))
        self.assertFalse(is_safe_automatic_change("Assistant Professor", "Professor", "whitespace"))

    def test_safe_llm_change_updates_exact_original_value(self):
        con = self.connection()
        con.execute("INSERT INTO cv_entries VALUES (7, ' Fellow  ', 'Work')")
        issues = [{
            "record_type": "cv_entry", "record_id": 7, "field": "title",
            "old_value": " Fellow  ", "new_value": "Fellow", "category": "whitespace",
        }]
        self.assertEqual(apply_corrections(con, issues), 1)
        self.assertEqual(con.execute("SELECT title FROM cv_entries WHERE id=7").fetchone()[0], "Fellow")
        self.assertTrue(issues[0]["applied"])

    def test_audit_applies_deterministic_fix_but_only_reports_semantic_llm_issue(self):
        con = self.connection()
        con.execute("INSERT INTO person VALUES (1, ' Ada  Lovelace ', 'Researcher')")

        def fake_llm(prompt, schema, settings):
            self.assertIn("Ada Lovelace", prompt)
            return {
                "summary": "One item should be reviewed.",
                "issues": [{
                    "record_type": "person", "record_id": 1, "field": "position_title",
                    "old_value": "Researcher", "new_value": "Professor",
                    "category": "possible_factual_error", "confidence": 0.9,
                    "reason": "Possible outdated title.",
                }],
            }, None

        result = run_export_quality_audit(con, Path("missing.docx"), fake_llm, {})
        row = con.execute("SELECT full_name, position_title FROM person WHERE id=1").fetchone()
        self.assertEqual(tuple(row), ("Ada Lovelace", "Researcher"))
        self.assertEqual(result["applied_count"], 1)
        self.assertEqual(result["review_count"], 1)

    def test_extracts_visible_docx_text(self):
        xml = b'''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Hello CV</w:t></w:r></w:p></w:body></w:document>'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.docx"
            with zipfile.ZipFile(path, "w") as package:
                package.writestr("word/document.xml", xml)
            self.assertEqual(extract_docx_text(path), "Hello CV")


if __name__ == "__main__":
    unittest.main()
