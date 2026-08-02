import sqlite3
import unittest

from vitamine.llm_routing import settings_for_llm_task
from vitamine.scripts.build_long_cv import _formal_four_columns
from vitamine.scripts.import_uploaded_cv import (
    apply_generation_controls,
    coalesce_numbered_citations,
    existing_entry_id,
    llm_text_chunks,
    split_compound_education_entry,
)


class CvImportAndExportTests(unittest.TestCase):
    def test_task_models_only_override_the_two_configured_cv_workloads(self):
        settings = {
            "provider": "openai",
            "api_model": "gpt-5.4-nano",
            "task_models": {
                "cv_import": "gpt-5.6-luna",
                "custom_template_analysis": "gpt-5.6-luna",
            },
        }
        self.assertEqual(settings_for_llm_task(settings, "cv_import")["api_model"], "gpt-5.6-luna")
        self.assertEqual(
            settings_for_llm_task(settings, "custom_template_analysis")["api_model"],
            "gpt-5.6-luna",
        )
        self.assertEqual(settings_for_llm_task(settings, "enrichment")["api_model"], "gpt-5.4-nano")
        self.assertEqual(settings["api_model"], "gpt-5.4-nano")

    def test_gpt5_generation_controls_use_reasoning_compatible_parameters(self):
        body = {}
        apply_generation_controls(
            body,
            {"api_reasoning_effort": "low", "api_max_tokens": "8192"},
            "gpt-5.4-nano",
        )
        self.assertEqual(body, {"reasoning_effort": "low", "max_completion_tokens": 8192})

    def test_legacy_generation_controls_remain_compatible(self):
        body = {}
        apply_generation_controls(body, {"api_max_tokens": "4096"}, "gpt-4.1-mini")
        self.assertEqual(body, {"temperature": 0, "max_tokens": 4096})

    def test_compound_md_phd_entry_is_split_without_losing_programs(self):
        entry = {
            "section_key": "education",
            "start_date": "09/27/2012",
            "end_date": "12/09/2016",
            "title": "MD, PhD",
            "organization": "Albert-Ludwigs-Universität Freiburg; Charité Berlin",
            "description": (
                "MD in Medicine from Albert-Ludwigs-Universität Freiburg; "
                "PhD in Medical Neurosciences from Charité Berlin"
            ),
            "raw_text": (
                "12/9/2016  PhD  Medical Neurosciences  Charité Berlin\n"
                "09/27/2012 MD Medicine Albert-Ludwigs-Universität Freiburg"
            ),
            "confidence": "high",
        }
        rows = split_compound_education_entry(entry)
        self.assertEqual(
            [(row["title"], row["start_date"], row["description"], row["organization"]) for row in rows],
            [
                ("MD", "09/27/2012", "Medicine", "Albert-Ludwigs-Universität Freiburg"),
                ("PhD", "12/9/2016", "Medical Neurosciences", "Charité Berlin"),
            ],
        )

    def test_ambiguous_compound_degree_entry_is_preserved(self):
        entry = {
            "section_key": "education",
            "title": "MD, PhD",
            "description": "Two doctoral qualifications",
            "raw_text": "MD, PhD",
        }
        self.assertEqual(split_compound_education_entry(entry), [entry])

    def test_honor_duplicate_is_found_by_semantics_not_only_raw_text(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.execute(
            """
            CREATE TABLE cv_entries (
              id INTEGER PRIMARY KEY,
              section_key TEXT,
              title TEXT,
              organization TEXT,
              start_date TEXT,
              end_date TEXT,
              raw_text TEXT
            )
            """
        )
        con.execute(
            """
            INSERT INTO cv_entries
              (id, section_key, title, organization, start_date, raw_text)
            VALUES (7, 'honors', 'Poster Award', 'Example Society', '2020', 'Original wording')
            """
        )
        duplicate = existing_entry_id(
            con,
            {
                "section_key": "honors",
                "title": "Poster award",
                "organization": "Example Society",
                "start_date": "2020",
                "raw_text": "Different narrative wording",
            },
        )
        self.assertEqual(duplicate, 7)

    def test_single_description_value_populates_middle_formal_column(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.execute(
            """
            CREATE TABLE item (
              start_date TEXT, end_date TEXT, title TEXT, organization TEXT,
              description TEXT, role TEXT, location TEXT, raw_text TEXT
            )
            """
        )
        row = con.execute(
            """
            SELECT '2020' AS start_date, '' AS end_date, 'Poster Award' AS title,
                   'Example Society' AS organization, 'Top 1%' AS description,
                   '' AS role, '' AS location, '' AS raw_text
            """
        ).fetchone()
        self.assertEqual(_formal_four_columns(row), ["2020", "Poster Award", "Top 1%", "Example Society"])

    def test_pandoc_grid_table_publications_are_split_into_individual_citations(self):
        markdown = """
+-----------------------------+
| 41. First Author. (2020). First paper. https://doi.org/10.1000/first
|                             |
| 42. Second Author. (2021). Second paper. https://doi.org/10.1000/second
+-----------------------------+
"""
        citations = coalesce_numbered_citations(markdown)
        self.assertEqual(len(citations), 2)
        self.assertTrue(citations[0].startswith("41. First Author"))
        self.assertTrue(citations[1].startswith("42. Second Author"))

    def test_llm_chunks_can_omit_deterministically_parsed_publication_sections(self):
        text = """EDUCATION
2020 Example degree

JOURNAL PUBLICATIONS
1. Example Author. (2024). Example publication.
"""
        all_chunks = llm_text_chunks(text)
        nonpublication_chunks = llm_text_chunks(text, include_publication_sections=False)
        self.assertTrue(any("Example publication" in chunk for chunk in all_chunks))
        self.assertFalse(any("Example publication" in chunk for chunk in nonpublication_chunks))
        self.assertTrue(any("Example degree" in chunk for chunk in nonpublication_chunks))


if __name__ == "__main__":
    unittest.main()
