import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vitamine.deployment import (
    deployment_config,
    llm_user_configuration_allowed,
    managed_llm,
    skip_llm_onboarding,
)


class DeploymentConfigTests(unittest.TestCase):
    def tearDown(self):
        deployment_config.cache_clear()

    def test_desktop_profile_keeps_user_llm_configuration(self):
        profile = Path(__file__).resolve().parents[1] / "config" / "vitamine-desktop.json"
        with patch.dict(os.environ, {"VITAMINE_DEPLOYMENT_CONFIG": str(profile)}):
            deployment_config.cache_clear()
            self.assertFalse(managed_llm())
            self.assertTrue(llm_user_configuration_allowed())
            self.assertFalse(skip_llm_onboarding())

    def test_hosted_profile_manages_llm_and_skips_configuration_tour(self):
        profile = Path(__file__).resolve().parents[1] / "deploy" / "strato" / "vitamine-hosted.json"
        with patch.dict(os.environ, {"VITAMINE_DEPLOYMENT_CONFIG": str(profile)}):
            deployment_config.cache_clear()
            self.assertTrue(managed_llm())
            self.assertFalse(llm_user_configuration_allowed())
            self.assertTrue(skip_llm_onboarding())

    def test_invalid_profile_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "invalid.json"
            profile.write_text(json.dumps({"mode": "hosted"}), encoding="utf-8")
            with patch.dict(os.environ, {"VITAMINE_DEPLOYMENT_CONFIG": str(profile)}):
                deployment_config.cache_clear()
                with self.assertRaises(RuntimeError):
                    deployment_config()
