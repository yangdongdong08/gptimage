import json
import unittest
from unittest.mock import patch

from utils import sentinel


class FakeResponse:
    def __init__(self, status_code=200, text="", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json_data = {} if json_data is None else json_data

    def json(self):
        return self._json_data


class FakeSession:
    def __init__(self):
        self.post_calls = []

    def post(self, url, data=None, headers=None, timeout=None, verify=None):
        self.post_calls.append(
            {
                "url": url,
                "data": data,
                "headers": headers,
                "timeout": timeout,
                "verify": verify,
            }
        )
        return FakeResponse(
            status_code=200,
            text='{"token":"challenge-token","proofofwork":{"required":false},"turnstile":{"required":false},"so":{"required":false}}',
            json_data={
                "token": "challenge-token",
                "proofofwork": {"required": False},
                "turnstile": {"required": False},
                "so": {"required": False},
            },
        )


class SentinelSdkTests(unittest.TestCase):
    def test_sdk_url_accepts_relative_official_path(self):
        value = sentinel._validated_sentinel_url("/sentinel/build-1/sdk.js")
        self.assertEqual(value, "https://sentinel.openai.com/sentinel/build-1/sdk.js")

    def test_sdk_url_rejects_untrusted_host(self):
        with self.assertRaisesRegex(RuntimeError, "sentinel_sdk_url_not_allowed"):
            sentinel._validated_sentinel_url("https://example.com/sentinel/build-1/sdk.js")

    def test_build_sentinel_artifacts_uses_sdk_prepare_token_for_req(self):
        session = FakeSession()
        with patch.object(
            sentinel,
            "_load_sentinel_sdk_assets",
            return_value=("https://sentinel.openai.com/sentinel/20260219f9f6/sdk.js", "var SentinelSDK={};"),
        ), patch.object(
            sentinel,
            "_run_sentinel_sdk_runner",
            side_effect=[
                {"prepare_token": "sdk-prepare-token"},
                {"token": "sdk-final-token", "so_token": "sdk-so-token"},
            ],
        ):
            artifacts = sentinel.build_sentinel_artifacts(session, "device-1", "oauth_create_account")

        sent_body = json.loads(session.post_calls[0]["data"])
        self.assertEqual(sent_body["p"], "sdk-prepare-token")
        self.assertEqual(sent_body["id"], "device-1")
        self.assertEqual(sent_body["flow"], "oauth_create_account")
        self.assertEqual(artifacts.token, "sdk-final-token")
        self.assertEqual(artifacts.so_token, "sdk-so-token")
        self.assertEqual(artifacts.oai_sc_value, "0challenge-token")
        self.assertEqual(artifacts.sdk_version, "20260219f9f6")


if __name__ == "__main__":
    unittest.main()
