import unittest
from unittest.mock import patch

from vitamine.profile_resolver import cv_profile_candidates, search_openalex, search_semantic_scholar, search_wikidata


class ProfileResolverTests(unittest.TestCase):
    def test_repeated_google_scholar_links_from_uk_host_are_verified(self):
        text = """
[Paper one](http://scholar.google.co.uk/citations?user=q_4u0aoAAAAJ&citation_for_view=one)
[Paper two](https://scholar.google.co.uk/citations?hl=en&user=q_4u0aoAAAAJ&citation_for_view=two)
[Paper three](https://scholar.google.co.uk/citations?hl=en&user=q_4u0aoAAAAJ&citation_for_view=three)
"""
        candidates = cv_profile_candidates(text)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["platform"], "Google Scholar")
        self.assertEqual(candidate["identifier_value"], "q_4u0aoAAAAJ")
        self.assertEqual(candidate["url"], "https://scholar.google.com/citations?user=q_4u0aoAAAAJ")
        self.assertTrue(candidate["auto_accept"])
        self.assertEqual(candidate["evidence"]["cv_link_occurrences"], 3)

    def test_single_profile_link_is_left_for_review(self):
        candidates = cv_profile_candidates(
            "Profile: https://www.researchgate.net/profile/Example-Researcher"
        )
        self.assertEqual(len(candidates), 1)
        self.assertFalse(candidates[0]["auto_accept"])
        self.assertEqual(candidates[0]["confidence"], "medium")

    @patch("vitamine.profile_resolver._get_json")
    def test_openalex_requires_publication_overlap_before_returning_candidate(self, get_json):
        get_json.side_effect = [
            {
                "results": [
                    {
                        "id": "https://openalex.org/A123",
                        "last_known_institutions": [{"display_name": "Example University"}],
                        "ids": {"orcid": "https://orcid.org/0000-0001-2345-6789"},
                    },
                    {"id": "https://openalex.org/A999", "last_known_institutions": []},
                ]
            },
            {"results": [{"title": "Shared first paper"}, {"title": "Shared second paper"}]},
            {"results": [{"title": "Unrelated work"}]},
        ]
        rows = search_openalex(
            "Example Researcher",
            "Example University",
            [{"title": "Shared first paper"}, {"title": "Shared second paper"}],
        )
        self.assertEqual([(row["platform"], row["auto_accept"]) for row in rows], [("OpenAlex", True), ("ORCID", True)])

    @patch("vitamine.profile_resolver._get_json")
    def test_semantic_scholar_ambiguous_match_stays_in_review(self, get_json):
        get_json.return_value = {
            "data": [
                {
                    "authorId": "42",
                    "url": "https://www.semanticscholar.org/author/42",
                    "affiliations": [],
                    "papers": [{"title": "Only shared paper"}],
                }
            ]
        }
        rows = search_semantic_scholar(
            "Example Researcher", "Example University", [{"title": "Only shared paper"}]
        )
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["auto_accept"])
        self.assertEqual(rows[0]["confidence"], "medium")

    @patch("vitamine.profile_resolver._get_json")
    def test_wikidata_profiles_are_verified_by_matching_orcid(self, get_json):
        get_json.side_effect = [
            {"search": [{"id": "Q123"}]},
            {
                "entities": {
                    "Q123": {
                        "claims": {
                            "P496": [{"mainsnak": {"datavalue": {"value": "0000-0001-2345-6789"}}}],
                            "P1960": [{"mainsnak": {"datavalue": {"value": "ScholarId"}}}],
                        }
                    }
                }
            },
        ]
        rows = search_wikidata("Example Researcher", {"0000-0001-2345-6789"})
        scholar = next(row for row in rows if row["platform"] == "Google Scholar")
        self.assertTrue(scholar["auto_accept"])
        self.assertEqual(scholar["identifier_value"], "ScholarId")


if __name__ == "__main__":
    unittest.main()
