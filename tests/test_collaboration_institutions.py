import unittest
from unittest.mock import patch

from vitamine.scripts.enrich_publications_by_doi import (
    citation_sample_limit,
    openalex_collaboration_rows,
    pending_enrichment_clause,
)


FRANKLIN_ID = "https://openalex.org/I53236636"
CHARITE_ID = "https://openalex.org/I7877124"
CHARITE_ROR = "https://ror.org/001w7jn25"


def institution_details(institution_id):
    if institution_id == FRANKLIN_ID:
        return {
            "id": FRANKLIN_ID,
            "display_name": "Franklin University",
            "display_name_alternatives": ["Franklin University"],
            "display_name_acronyms": [],
            "ror": "https://ror.org/00x9w0n02",
            "geo": {
                "country_code": "US",
                "country": "United States",
                "latitude": 39.96,
                "longitude": -83.0,
            },
        }
    return {
        "id": CHARITE_ID,
        "display_name": "Charité - Universitätsmedizin Berlin",
        "display_name_alternatives": ["Charité"],
        "display_name_acronyms": [],
        "ror": CHARITE_ROR,
        "geo": {
            "country_code": "DE",
            "country": "Germany",
            "latitude": 52.52,
            "longitude": 13.41,
        },
    }


def charite_ror_match(_raw_affiliation):
    return {
        "id": CHARITE_ROR,
        "names": [
            {
                "value": "Charité - Universitätsmedizin Berlin",
                "types": ["ror_display", "label"],
            }
        ],
        "locations": [
            {
                "geonames_details": {
                    "country_code": "DE",
                    "country_name": "Germany",
                    "lat": 52.52,
                    "lng": 13.41,
                }
            }
        ],
    }


class CollaborationInstitutionTests(unittest.TestCase):
    def test_citation_sample_limit_scales_but_stays_bounded(self):
        self.assertEqual(citation_sample_limit(12), 12)
        self.assertEqual(citation_sample_limit(100), 50)
        self.assertEqual(citation_sample_limit(2000), 100)

    def test_normal_enrichment_backfills_missing_citation_geography(self):
        clause = pending_enrichment_clause(False)
        self.assertIn("openalex_citation_geography_enriched_at IS NULL", clause)
        self.assertEqual(pending_enrichment_clause(True), "")

    @patch(
        "vitamine.scripts.enrich_publications_by_doi.ror_affiliation_match",
        side_effect=charite_ror_match,
    )
    @patch(
        "vitamine.scripts.enrich_publications_by_doi.openalex_institution",
        side_effect=institution_details,
    )
    def test_ror_replaces_false_campus_name_match(self, _institution, _ror):
        raw = (
            "Department of Neurology, Charité - Universitätsmedizin Berlin, "
            "Campus Benjamin Franklin, Berlin, Germany"
        )
        payload = {
            "id": "https://openalex.org/W1",
            "display_name": "Example work",
            "publication_year": 2016,
            "authorships": [
                {
                    "author_position": "middle",
                    "author": {"display_name": "Example Coauthor"},
                    "raw_affiliation_strings": [raw],
                    "institutions": [
                        {
                            "id": FRANKLIN_ID,
                            "display_name": "Franklin University",
                            "ror": "https://ror.org/00x9w0n02",
                        },
                        {
                            "id": CHARITE_ID,
                            "display_name": "Charité - Universitätsmedizin Berlin",
                            "ror": CHARITE_ROR,
                        },
                    ],
                    "affiliations": [
                        {
                            "raw_affiliation_string": raw,
                            "institution_ids": [FRANKLIN_ID],
                        },
                        {
                            "raw_affiliation_string": raw,
                            "institution_ids": [CHARITE_ID],
                        },
                    ],
                }
            ],
        }

        rows = openalex_collaboration_rows(
            1,
            {"title": "Example work", "year": "2016"},
            payload,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["institution_id"], CHARITE_ROR)
        self.assertEqual(rows[0]["institution_name"], "Charité - Universitätsmedizin Berlin")
        self.assertEqual(rows[0]["country_code"], "DE")

    @patch(
        "vitamine.scripts.enrich_publications_by_doi.ror_affiliation_match",
        return_value={},
    )
    @patch(
        "vitamine.scripts.enrich_publications_by_doi.openalex_institution",
        side_effect=institution_details,
    )
    def test_weak_openalex_match_is_left_unmatched(self, _institution, _ror):
        raw = "Department of Psychiatry, Campus Benjamin Franklin"
        payload = {
            "id": "https://openalex.org/W2",
            "display_name": "Example work",
            "publication_year": 2015,
            "authorships": [
                {
                    "author_position": "middle",
                    "author": {"display_name": "Example Coauthor"},
                    "institutions": [
                        {
                            "id": FRANKLIN_ID,
                            "display_name": "Franklin University",
                            "ror": "https://ror.org/00x9w0n02",
                        }
                    ],
                    "affiliations": [
                        {
                            "raw_affiliation_string": raw,
                            "institution_ids": [FRANKLIN_ID],
                        }
                    ],
                }
            ],
        }

        rows = openalex_collaboration_rows(
            2,
            {"title": "Example work", "year": "2015"},
            payload,
        )

        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
