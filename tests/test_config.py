from __future__ import annotations

import pytest
from pydantic import ValidationError

from visa_jobs_api.config import Settings


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = dict(
        gmail_address="a@b.com",
        gmail_app_password="pw",
        digest_recipients="me@example.com",
        # Explicit None, not just omitted -- otherwise pydantic-settings
        # falls back to whatever's actually in this machine's real .env
        # (which has a real proxy configured), making these tests'
        # outcomes depend on developer-machine state.
        indeed_selenium_proxy_host=None,
        indeed_selenium_proxy_port=None,
        indeed_selenium_proxy_username=None,
        indeed_selenium_proxy_password=None,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def test_gmail_app_password_strips_the_spaces_google_displays_it_with() -> None:
    # Google's UI shows app passwords as 4 space-separated groups for
    # readability; pasting that verbatim into an env var is an easy, silent
    # way to end up with a `535 Username and Password not accepted` SMTP
    # auth failure.
    settings = _settings(gmail_app_password="abcd efgh ijkl mnop")

    assert settings.gmail_app_password == "abcdefghijklmnop"


def test_gmail_app_password_without_spaces_is_unchanged() -> None:
    settings = _settings(gmail_app_password="abcdefghijklmnop")

    assert settings.gmail_app_password == "abcdefghijklmnop"


def test_indeed_selenium_proxy_settings_all_unset_is_valid() -> None:
    settings = _settings()

    assert settings.indeed_selenium_proxy_host is None


def test_indeed_selenium_proxy_settings_all_set_is_valid() -> None:
    settings = _settings(
        indeed_selenium_proxy_host="gw.example.com",
        indeed_selenium_proxy_port=823,
        indeed_selenium_proxy_username="user",
        indeed_selenium_proxy_password="pass",
    )

    assert settings.indeed_selenium_proxy_host == "gw.example.com"


def test_indeed_selenium_proxy_settings_partially_set_raises() -> None:
    # A host with no password (etc.) would otherwise surface as a
    # confusing failure deep inside selenium_client.py the next time
    # Indeed search runs, rather than a clear error at startup.
    with pytest.raises(ValidationError, match="must be all set or all unset"):
        _settings(indeed_selenium_proxy_host="gw.example.com")
