import unittest

from docx import Document

from vitamine.scripts import build_short_cv, build_ultrashort_tabular_cv
from vitamine.scripts.export_utils import configure_researcher_name, researcher_name_pattern


class ExportResearcherNameTests(unittest.TestCase):
    def setUp(self):
        configure_researcher_name({"display_name": "Jane Example", "full_name": "Jane Alice Example"})

    def test_matches_the_current_researcher_in_common_citation_forms(self):
        pattern = researcher_name_pattern()
        self.assertEqual(pattern.findall("Jane Example; Example J.; Example, J. A."), ["Jane Example", "Example J.", "Example, J. A."])
        self.assertEqual(pattern.findall("Andreas Horn; Horn A."), [])

    def test_compact_docx_exports_bold_only_the_current_researcher(self):
        value = "Andreas Horn, Jane Example, Example J., Horn A."
        for add_piece in (build_short_cv.add_docx_piece, build_ultrashort_tabular_cv.add_docx_piece):
            document = Document()
            paragraph = document.add_paragraph()
            add_piece(paragraph, value, bold_names=True)
            bold = "".join(run.text for run in paragraph.runs if run.bold)
            self.assertEqual(bold, "Jane ExampleExample J.")


if __name__ == "__main__":
    unittest.main()
