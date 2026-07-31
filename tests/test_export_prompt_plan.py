import unittest

from vitamine.app import validate_export_plan


class ExportPromptPlanTests(unittest.TestCase):
    def setUp(self):
        self.candidates = [
            {
                "id": 1,
                "title": "Older high-impact paper",
                "authors": "Researcher A, Researcher B",
                "venue": "Journal",
                "year": "2020",
                "impact_factor": 20,
                "authorship": "last",
                "score": 70,
            },
            {
                "id": 2,
                "title": "Required Rajamani paper",
                "authors": "Rajamani N, Researcher A",
                "venue": "Journal",
                "year": "2024",
                "impact_factor": 12,
                "authorship": "last",
                "score": 65,
            },
            {
                "id": 3,
                "title": "Recent paper",
                "authors": "Researcher A, Coauthor C",
                "venue": "Journal",
                "year": "2025",
                "impact_factor": 10,
                "authorship": "first",
                "score": 60,
            },
            {
                "id": 4,
                "title": "High-scoring middle-author paper",
                "authors": "Coauthor A, Researcher A, Coauthor B",
                "venue": "Journal",
                "year": "2025",
                "impact_factor": 30,
                "authorship": "other",
                "score": 90,
            },
        ]

    def test_unknown_ids_are_rejected_and_required_paper_is_kept_within_limit(self):
        plan = validate_export_plan(
            {
                "max_pages": 2,
                "max_publications": 2,
                "authorship_preference": "first_last",
                "recency_preference": "moderate",
                "impact_factor_preference": "strong",
                "selected_publication_ids": [1, 999, 3],
                "required_publication_ids": [2, 999],
                "section_strategy": "compact",
                "interpretation": "Compact grant CV",
                "warnings": [],
            },
            self.candidates,
            "Include Rajamani et al.",
            "vitamine.short-academic",
        )
        self.assertEqual(plan["selected_publication_ids"], [2, 1])
        self.assertEqual(plan["required_publication_ids"], [2])
        self.assertEqual(len(plan["selected_publications"]), 2)
        self.assertTrue(any("page limit" in warning for warning in plan["warnings"]))

    def test_authorship_constraint_is_enforced_after_llm_selection(self):
        plan = validate_export_plan(
            {
                "max_publications": 2,
                "authorship_preference": "first_last",
                "selected_publication_ids": [4, 3],
                "required_publication_ids": [],
            },
            self.candidates,
            "Use only first- or last-author work.",
            "vitamine.short-academic",
        )
        self.assertNotIn(4, plan["selected_publication_ids"])
        self.assertEqual(len(plan["selected_publication_ids"]), 2)

    def test_empty_model_selection_uses_ranked_safe_fallback(self):
        plan = validate_export_plan(
            {"max_publications": 2, "selected_publication_ids": [], "required_publication_ids": []},
            self.candidates,
            "Choose two strong papers.",
            "vitamine.one-page-scientific",
        )
        self.assertEqual(plan["selected_publication_ids"], [4, 1])


if __name__ == "__main__":
    unittest.main()
