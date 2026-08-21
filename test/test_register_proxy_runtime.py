import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from services.proxy_service import ClearanceBundle
from services.register import openai_register


class FakeResponse:
    def __init__(self, status_code=200, text="", headers=None, url="https://auth.openai.com/test", json_data=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.url = url
        self._json_data = {} if json_data is None else json_data

    def json(self):
        return self._json_data


class FakeCookieJar:
    def __init__(self):
        self.items = []

    def set(self, name, value, domain=None):
        self.items.append({"name": name, "value": value, "domain": domain})


class FakeSession:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.headers = {}
        self.cookies = FakeCookieJar()
        self.closed = False

    def close(self):
        self.closed = True


class FakeProxySettings:
    def __init__(self, bundle=None):
        self.bundle = bundle
        self.refreshed = False
        self.session_kwargs_calls = []
        self.build_headers_calls = []
        self.refresh_calls = []

    class _Profile:
        def __init__(self, enabled=True):
            self.clearance_enabled = enabled

    def build_session_kwargs(self, **kwargs):
        self.session_kwargs_calls.append(kwargs)
        return dict(kwargs, proxy="http://runtime.example:8118")

    def build_headers(self, headers=None, target_url="", proxy="", upstream=True, **kwargs):
        self.build_headers_calls.append({"target_url": target_url, "proxy": proxy, "upstream": upstream})
        merged = dict(headers or {})
        if self.refreshed and self.bundle and self.bundle.cookies:
            merged["Cookie"] = "; ".join(f"{key}={value}" for key, value in self.bundle.cookies.items())
        return merged

    def refresh_clearance(self, target_url="", proxy="", force=False, upstream=True, **kwargs):
        self.refresh_calls.append({"target_url": target_url, "proxy": proxy, "force": force, "upstream": upstream})
        self.refreshed = self.bundle is not None
        return self.bundle

    def get_profile(self, proxy="", upstream=True, **kwargs):
        return self._Profile(enabled=True)


class RegisterProxyRuntimeTests(unittest.TestCase):
    def test_create_session_uses_proxy_settings_without_breaking_existing_proxy_argument(self):
        fake_proxy = FakeProxySettings()
        created = []

        def fake_session_factory(**kwargs):
            session = FakeSession(**kwargs)
            created.append(session)
            return session

        with patch.object(openai_register, "proxy_settings", fake_proxy), patch.object(
            openai_register.requests,
            "Session",
            side_effect=fake_session_factory,
        ):
            session = openai_register.create_session("http://legacy-register.example:8080")

        self.assertIs(session, created[0])
        self.assertEqual(fake_proxy.session_kwargs_calls[0]["proxy"], "http://legacy-register.example:8080")
        self.assertTrue(fake_proxy.session_kwargs_calls[0]["upstream"])
        self.assertEqual(fake_proxy.session_kwargs_calls[0]["impersonate"], "chrome")
        self.assertFalse(fake_proxy.session_kwargs_calls[0]["verify"])
        self.assertEqual(session.kwargs["proxy"], "http://runtime.example:8118")

    def test_cloudflare_without_clearance_keeps_clear_register_error(self):
        fake_proxy = FakeProxySettings(bundle=None)
        cf_response = FakeResponse(
            status_code=403,
            text="<html><title>Just a moment...</title></html>",
            headers={"server": "cloudflare", "content-type": "text/html"},
            url="https://auth.openai.com/api/accounts/authorize",
        )

        with patch.object(openai_register, "proxy_settings", fake_proxy), patch.object(
            openai_register,
            "create_session",
            return_value=FakeSession(),
        ), patch.object(openai_register, "request_with_local_retry", return_value=(cf_response, "")):
            registrar = openai_register.PlatformRegistrar(proxy="http://legacy-register.example:8080")
            with self.assertRaisesRegex(RuntimeError, "Cloudflare") as ctx:
                registrar._platform_authorize("user@example.com", 1)

        self.assertEqual(len(fake_proxy.refresh_calls), 1)
        self.assertIn("status=403", str(ctx.exception))
        self.assertIn("Just a moment", str(ctx.exception))

    def test_openai_html_behind_cloudflare_is_not_treated_as_challenge(self):
        response = FakeResponse(
            status_code=200,
            text="""
            <!DOCTYPE html><html lang=\"en-US\"><head>
            <title>Create a password - OpenAI</title>
            </head><body>OpenAI account page</body></html>
            """,
            headers={"server": "cloudflare", "content-type": "text/html; charset=utf-8"},
            url="https://auth.openai.com/create-account/password",
        )

        self.assertFalse(openai_register._is_cloudflare_challenge(response))

    def test_platform_authorize_uses_passwordless_signup_and_detects_email_verification(self):
        response = FakeResponse(
            status_code=200,
            text="<html></html>",
            headers={"content-type": "text/html; charset=utf-8"},
            url="https://auth.openai.com/email-verification",
        )
        request_calls = []

        def fake_request(session, method, url, retry_attempts=3, **kwargs):
            request_calls.append({"method": method, "url": url})
            return response, ""

        with patch.object(openai_register, "create_session", return_value=FakeSession()), patch.object(
            openai_register,
            "request_with_local_retry",
            side_effect=fake_request,
        ):
            registrar = openai_register.PlatformRegistrar(proxy="")
            registrar._platform_authorize("new@example.com", 1)

        query = parse_qs(urlparse(request_calls[0]["url"]).query)
        self.assertEqual(query["screen_hint"], ["login_or_signup"])
        self.assertTrue(registrar.passwordless_signup)

    def test_register_skips_password_endpoint_for_passwordless_signup(self):
        mailbox = {"address": "new@example.com", "label": "test"}
        tokens = {"access_token": "access", "refresh_token": "refresh", "id_token": "id"}

        with patch.object(openai_register, "create_session", return_value=FakeSession()), patch.object(
            openai_register,
            "create_mailbox",
            return_value=mailbox,
        ), patch.object(openai_register, "wait_for_code", return_value="123456"), patch.object(
            openai_register.mail_provider,
            "mark_mailbox_result",
        ), patch.object(openai_register, "record_mailbox_result"):
            registrar = openai_register.PlatformRegistrar(proxy="")

            def authorize(email, index):
                registrar.passwordless_signup = True

            with patch.object(registrar, "_platform_authorize", side_effect=authorize), patch.object(
                registrar,
                "_register_user",
            ) as register_user, patch.object(registrar, "_send_otp") as send_otp, patch.object(
                registrar,
                "_validate_otp",
            ), patch.object(registrar, "_create_account"), patch.object(
                registrar,
                "_exchange_registered_tokens",
                return_value=tokens,
            ):
                result = registrar.register(1)

        register_user.assert_not_called()
        send_otp.assert_not_called()
        self.assertEqual(result["password"], "")
        self.assertEqual(result["access_token"], "access")

    def test_start_passwordless_signup_posts_send_otp_endpoint(self):
        request_calls = []

        def fake_request(session, method, url, retry_attempts=3, **kwargs):
            request_calls.append({"method": method, "url": url, "headers": kwargs.get("headers") or {}})
            return FakeResponse(status_code=200, text="{}", json_data={}), ""

        with patch.object(openai_register, "create_session", return_value=FakeSession()), patch.object(
            openai_register,
            "request_with_local_retry",
            side_effect=fake_request,
        ):
            registrar = openai_register.PlatformRegistrar(proxy="")
            registrar._start_passwordless_signup(1)

        self.assertEqual(request_calls[0]["method"], "post")
        self.assertEqual(request_calls[0]["url"], "https://auth.openai.com/api/accounts/passwordless/send-otp")
        self.assertTrue(registrar.passwordless_signup)

    def test_cloudflare_challenge_refreshes_clearance_and_retries_once_with_matching_headers(self):
        bundle = ClearanceBundle(
            target_host="auth.openai.com",
            proxy_url="http://runtime.example:8118",
            cookies={"cf_clearance": "flare-token"},
            user_agent="Flare UA",
        )
        fake_proxy = FakeProxySettings(bundle=bundle)
        responses = [
            FakeResponse(
                status_code=403,
                text="<html><title>Just a moment...</title></html>",
                headers={"server": "cloudflare", "content-type": "text/html"},
                url="https://auth.openai.com/api/accounts/authorize",
            ),
            FakeResponse(status_code=200, text="{}", headers={"content-type": "application/json"}),
        ]
        request_calls = []

        def fake_request(session, method, url, retry_attempts=3, **kwargs):
            request_calls.append({"method": method, "url": url, "headers": dict(kwargs.get("headers") or {})})
            return responses.pop(0), ""

        with patch.object(openai_register, "proxy_settings", fake_proxy), patch.object(
            openai_register,
            "create_session",
            return_value=FakeSession(),
        ), patch.object(openai_register, "request_with_local_retry", side_effect=fake_request):
            registrar = openai_register.PlatformRegistrar(proxy="http://legacy-register.example:8080")
            registrar._platform_authorize("user@example.com", 1)

        self.assertEqual(len(request_calls), 2)
        self.assertEqual(len(fake_proxy.refresh_calls), 1)
        retry_headers = {key.lower(): value for key, value in request_calls[1]["headers"].items()}
        self.assertEqual(retry_headers["user-agent"], "Flare UA")
        self.assertEqual(retry_headers["cookie"], "cf_clearance=flare-token")
        self.assertEqual(fake_proxy.refresh_calls[0]["target_url"], openai_register.auth_base)
        self.assertEqual(fake_proxy.refresh_calls[0]["proxy"], "http://legacy-register.example:8080")
        self.assertTrue(fake_proxy.refresh_calls[0]["force"])

    def test_refresh_failure_reports_cloudflare_detail_without_infinite_retry(self):
        fake_proxy = FakeProxySettings(bundle=None)
        cf_response = FakeResponse(
            status_code=403,
            text="<html><title>Just a moment...</title><body>challenge body</body></html>",
            headers={"server": "cloudflare", "content-type": "text/html"},
            url="https://auth.openai.com/api/accounts/authorize",
        )
        request_calls = []

        def fake_request(session, method, url, retry_attempts=3, **kwargs):
            request_calls.append({"method": method, "url": url})
            return cf_response, ""

        with patch.object(openai_register, "proxy_settings", fake_proxy), patch.object(
            openai_register,
            "create_session",
            return_value=FakeSession(),
        ), patch.object(openai_register, "request_with_local_retry", side_effect=fake_request):
            registrar = openai_register.PlatformRegistrar(proxy="")
            with self.assertRaisesRegex(RuntimeError, "Cloudflare") as ctx:
                registrar._platform_authorize("user@example.com", 1)

        self.assertEqual(len(request_calls), 1)
        self.assertEqual(len(fake_proxy.refresh_calls), 1)
        message = str(ctx.exception)
        self.assertIn("status=403", message)
        self.assertIn("challenge body", message)

    def test_validate_otp_follows_continue_url(self):
        with patch.object(
            openai_register,
            "create_session",
            return_value=FakeSession(),
        ), patch.object(
            openai_register,
            "validate_otp",
            return_value=(
                FakeResponse(
                    status_code=200,
                    text='{"continue_url":"https://auth.openai.com/authorize/continue?state=test"}',
                    json_data={"continue_url": "https://auth.openai.com/authorize/continue?state=test"},
                ),
                "",
            ),
        ):
            registrar = openai_register.PlatformRegistrar(proxy="")
            seen = []
            with patch.object(registrar, "_authorize_continue", side_effect=lambda url, index: seen.append((url, index))):
                registrar._validate_otp("123456", 7)
        self.assertEqual(seen, [("https://auth.openai.com/authorize/continue?state=test", 7)])

    def test_create_account_adds_sentinel_and_so_headers(self):
        with patch.object(
            openai_register,
            "create_session",
            return_value=FakeSession(),
        ):
            registrar = openai_register.PlatformRegistrar(proxy="")
        request_calls = []

        def fake_request(session, method, url, retry_attempts=3, **kwargs):
            request_calls.append(kwargs.get("headers") or {})
            return FakeResponse(status_code=200, text='{"continue_url":"https://platform.openai.com/auth/callback?code=abc"}', json_data={"continue_url": "https://platform.openai.com/auth/callback?code=abc"}), ""

        artifacts = openai_register.SentinelArtifacts(
            token="sentinel-token",
            so_token="so-token",
            oai_sc_value="0cookie",
            sdk_version="sdk-test",
            observer_timeout_ms=5000,
        )
        with patch.object(registrar, "_build_sentinel", return_value=artifacts), patch.object(
            openai_register,
            "request_with_local_retry",
            side_effect=fake_request,
        ):
            registrar._create_account("Demo User", "2000-01-01", 3)

        lowered = {key.lower(): value for key, value in request_calls[0].items()}
        self.assertEqual(lowered["openai-sentinel-token"], "sentinel-token")
        self.assertEqual(lowered["openai-sentinel-so-token"], "so-token")
        self.assertEqual(registrar.platform_auth_code, "abc")


if __name__ == "__main__":
    unittest.main()
