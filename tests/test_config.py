from __future__ import annotations

from visa_jobs_api.config import Settings


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = dict(
        hf_token="fake",
        gmail_address="a@b.com",
        gmail_app_password="pw",
        digest_recipients="me@example.com",
        decodo_username="decodo-user",
        decodo_password="decodo-pass",
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
