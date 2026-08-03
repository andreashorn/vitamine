import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Inches

from vitamine.scripts.build_short_cv import (
    add_compact_heading,
    remove_table_borders,
    set_table_width,
    unique_detail_parts,
)
from vitamine.scripts import build_short_cv


class ShortCvDocxTests(unittest.TestCase):
    def test_table_geometry_uses_word_twips_and_no_visible_grid(self):
        document = Document()
        table = document.add_table(rows=1, cols=2)
        table.style = None
        widths = [Inches(1.45), Inches(5.55)]
        remove_table_borders(table)
        set_table_width(table, widths)

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "short.docx"
            document.save(path)
            rendered = Document(path)
            table = rendered.tables[0]
            expected = [str(int(width.twips)) for width in widths]
            grid = [column.get(qn("w:w")) for column in table._tbl.tblGrid]
            cells = [
                cell._tc.get_or_add_tcPr().find(qn("w:tcW")).get(qn("w:w"))
                for cell in table.rows[0].cells
            ]
            borders = table._tbl.tblPr.first_child_found_in("w:tblBorders")

            self.assertEqual(grid, expected)
            self.assertEqual(cells, expected)
            self.assertEqual(
                table._tbl.tblPr.first_child_found_in("w:tblW").get(qn("w:w")),
                str(sum(int(width.twips) for width in widths)),
            )
            self.assertTrue(all(edge.get(qn("w:val")) == "nil" for edge in borders))

    def test_section_heading_has_a_plain_top_rule(self):
        document = Document()
        add_compact_heading(document, "Selected Publications")
        paragraph = document.paragraphs[0]
        top = paragraph._p.pPr.find(qn("w:pBdr")).find(qn("w:top"))

        self.assertEqual(top.get(qn("w:val")), "single")
        self.assertEqual(top.get(qn("w:color")), "111111")

    def test_recent_mentoring_is_included_when_none_is_explicitly_selected(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "short.sqlite"
            import sqlite3

            with sqlite3.connect(database) as connection:
                connection.executescript((Path(__file__).parents[1] / "vitamine" / "schema.sql").read_text())
                connection.execute(
                    "INSERT INTO documents (id, slug, title, source_path, source_format, imported_at) "
                    "VALUES (1, 'test', 'Test', 'test.docx', 'docx', '2026-08-03')"
                )
                connection.execute(
                    "INSERT INTO cv_entries (document_id, section_key, start_date, title, raw_text, include_long, include_short) "
                    "VALUES (1, 'education', '2020', 'Selected education', 'Selected education', 1, 1)"
                )
                for year in range(2018, 2025):
                    connection.execute(
                        "INSERT INTO cv_entries (document_id, section_key, start_date, title, raw_text, include_long, include_short) "
                        "VALUES (1, 'mentoring', ?, ?, ?, 1, 0)",
                        (str(year), f'Trainee {year}', f'Trainee {year}'),
                    )

            with patch.object(build_short_cv, "DB", database):
                _person, entries, _publications = build_short_cv.load_data()

            mentoring = [row for row in entries if row["section_key"] == "mentoring"]
            self.assertEqual(len(mentoring), 6)
            self.assertEqual({row["start_date"] for row in mentoring}, {"2019", "2020", "2021", "2022", "2023", "2024"})

    def test_repeated_entry_details_are_removed_without_name_specific_rules(self):
        self.assertEqual(
            unique_detail_parts(["Trainee / PhD / University", "Trainee / PhD / University", "Supervisor"]),
            ["Trainee / PhD / University", "Supervisor"],
        )


if __name__ == "__main__":
    unittest.main()
