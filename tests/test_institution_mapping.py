import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vitamine.app import (
    INSTITUTION_MAPPING_FINGERPRINT_SETTING,
    institution_mapping_needed,
    map_institution_automatically,
)
from vitamine.paths import create_blank_database


class InstitutionMappingTests(unittest.TestCase):
    def blank_database(self, directory: str) -> Path:
        path = Path(directory) / "mapping.vitamine"
        create_blank_database(path)
        return path

    def connection(self, path: Path) -> sqlite3.Connection:
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        return con

    def test_saved_institution_is_mapped_without_orcid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.blank_database(directory)
            with self.connection(path) as con:
                con.execute(
                    """
                    UPDATE person
                    SET own_institution_name='University Hospital Cologne',
                        own_institution_country='Germany',
                        own_institution_country_code='DE'
                    WHERE id=1
                    """
                )
                con.commit()

            with patch(
                "vitamine.app.geocode_institution",
                return_value={
                    "latitude": 50.938,
                    "longitude": 6.956,
                    "country": "Germany",
                    "country_code": "DE",
                    "geocoded_query": "University Hospital Cologne, Germany, DE",
                },
            ) as geocode:
                result = map_institution_automatically(path)

            self.assertTrue(result["mapped"])
            geocode.assert_called_once()
            with self.connection(path) as con:
                person = con.execute("SELECT * FROM person WHERE id=1").fetchone()
                marker = con.execute(
                    "SELECT value FROM app_settings WHERE key=?",
                    (INSTITUTION_MAPPING_FINGERPRINT_SETTING,),
                ).fetchone()["value"]
            self.assertAlmostEqual(person["own_institution_latitude"], 50.938)
            self.assertEqual(person["own_institution_name"], "University Hospital Cologne")
            self.assertTrue(marker.startswith("auto:"))

    def test_complete_unmarked_coordinates_are_preserved_as_manual(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.blank_database(directory)
            with self.connection(path) as con:
                con.execute(
                    """
                    UPDATE person
                    SET own_institution_name='Manually placed institute',
                        own_institution_latitude=1.25,
                        own_institution_longitude=2.5
                    WHERE id=1
                    """
                )
                con.commit()
                self.assertFalse(institution_mapping_needed(con))

            with patch("vitamine.app.geocode_institution") as geocode:
                result = map_institution_automatically(path)
            self.assertFalse(result["mapped"])
            self.assertEqual(result["reason"], "not_needed")
            geocode.assert_not_called()

    def test_changing_an_automatically_mapped_institution_remaps_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.blank_database(directory)
            with self.connection(path) as con:
                con.execute(
                    "UPDATE person SET own_institution_name='First Institute' WHERE id=1"
                )
                con.commit()

            first_result = {
                "latitude": 1.0,
                "longitude": 2.0,
                "country": "Germany",
                "country_code": "DE",
                "geocoded_query": "First Institute",
            }
            second_result = {
                "latitude": 3.0,
                "longitude": 4.0,
                "country": "France",
                "country_code": "FR",
                "geocoded_query": "Second Institute",
            }
            with patch("vitamine.app.geocode_institution", side_effect=[first_result, second_result]):
                self.assertTrue(map_institution_automatically(path)["mapped"])
                with self.connection(path) as con:
                    con.execute(
                        "UPDATE person SET own_institution_name='Second Institute' WHERE id=1"
                    )
                    con.commit()
                    self.assertTrue(institution_mapping_needed(con))
                self.assertTrue(map_institution_automatically(path)["mapped"])

            with self.connection(path) as con:
                person = con.execute("SELECT * FROM person WHERE id=1").fetchone()
            self.assertEqual(person["own_institution_name"], "Second Institute")
            self.assertAlmostEqual(person["own_institution_latitude"], 3.0)

    def test_orcid_affiliation_supplies_missing_institution(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.blank_database(directory)
            with self.connection(path) as con:
                con.execute(
                    """
                    INSERT INTO person_identifiers
                      (person_id, platform, identifier_type, identifier_value, url, source)
                    VALUES
                      (1, 'ORCID', 'ORCID iD', '0000-0001-2345-6789',
                       'https://orcid.org/0000-0001-2345-6789', 'manual')
                    """
                )
                con.commit()

            affiliation = {
                "affiliation-group": [
                    {
                        "summaries": [
                            {
                                "employment-summary": {
                                    "organization": {
                                        "name": "ORCID University",
                                        "address": {
                                            "city": "Cologne",
                                            "country": "DE",
                                        },
                                    },
                                    "start-date": {"year": {"value": "2024"}},
                                    "end-date": None,
                                }
                            }
                        ]
                    }
                ]
            }
            geocoded = {
                "latitude": 50.94,
                "longitude": 6.96,
                "country": "Germany",
                "country_code": "DE",
                "geocoded_query": "ORCID University, Cologne, DE",
            }
            with (
                patch("vitamine.app.fetch_json_url", return_value=affiliation),
                patch("vitamine.app.geocode_institution", return_value=geocoded),
            ):
                result = map_institution_automatically(path)

            self.assertTrue(result["mapped"])
            self.assertEqual(result["source"], "ORCID public employments")
            with self.connection(path) as con:
                person = con.execute("SELECT * FROM person WHERE id=1").fetchone()
            self.assertEqual(person["own_institution_name"], "ORCID University")
            self.assertEqual(person["own_institution_country_code"], "DE")


if __name__ == "__main__":
    unittest.main()
