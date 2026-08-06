import io
import json
import unittest
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError

from vitamine.scripts.import_uploaded_cv import (
    call_openai_compatible,
    llm_extract,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


def provider_error(code, body):
    headers = Message()
    if code == 429:
        headers["Retry-After"] = "1"
    return HTTPError(
        "https://api.openai.com/v1/chat/completions",
        code,
        "provider error",
        headers,
        io.BytesIO(json.dumps(body).encode("utf-8")),
    )


class ProviderFailureTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "provider": "openai",
            "api_key": "sk-test-key",
            "api_model": "gpt-5.4-nano",
        }

    def test_rate_limit_retries_once_without_retaining_provider_body(self):
        private_value = "private-provider-rate-limit-detail"
        error = provider_error(429, {"error": {"code": "rate_limit_reached", "message": private_value}})
        with (
            patch(
                "vitamine.scripts.import_uploaded_cv.urllib.request.urlopen",
                side_effect=[error, FakeResponse({"person": {"full_name": "Test"}})],
            ) as urlopen,
            patch("vitamine.scripts.import_uploaded_cv.time.sleep") as sleep,
            patch("vitamine.scripts.import_uploaded_cv.random.uniform", return_value=0.1),
        ):
            result = call_openai_compatible("CV", self.settings)
        self.assertEqual(result["person"]["full_name"], "Test")
        self.assertEqual(urlopen.call_count, 2)
        self.assertGreaterEqual(sleep.call_args.args[0], 1)
        self.assertNotIn(private_value, json.dumps(result))

    def test_outage_uses_safe_heuristic_fallback_after_bounded_retry(self):
        private_value = "private-provider-outage-detail"
        error = provider_error(503, {"error": {"message": private_value}})
        with (
            patch(
                "vitamine.scripts.import_uploaded_cv.urllib.request.urlopen",
                side_effect=[error, error],
            ) as urlopen,
            patch("vitamine.scripts.import_uploaded_cv.time.sleep"),
        ):
            result, warning = llm_extract("CV", self.settings)
        self.assertIsNone(result)
        self.assertEqual(urlopen.call_count, 2)
        self.assertIn("temporarily unavailable", warning or "")
        self.assertNotIn(private_value, warning or "")

    def test_malformed_model_output_gets_one_repair_retry_then_safe_fallback(self):
        private_value = "private-malformed-model-output"
        malformed = FakeResponse({"choices": [{"message": {"content": private_value}}]})
        with (
            patch(
                "vitamine.scripts.import_uploaded_cv.urllib.request.urlopen",
                side_effect=[malformed, malformed],
            ) as urlopen,
            patch("vitamine.scripts.import_uploaded_cv.time.sleep"),
        ):
            result, warning = llm_extract("CV", self.settings)
        self.assertIsNone(result)
        self.assertEqual(urlopen.call_count, 2)
        self.assertIn("unusable structured response", warning or "")
        self.assertNotIn(private_value, warning or "")


if __name__ == "__main__":
    unittest.main()
