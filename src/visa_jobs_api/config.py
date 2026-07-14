"""Typed application settings, loaded once from the environment/.env.

Every configurable value (secrets, per-stage concurrency caps, pagination
limits) lives here rather than scattered `os.environ` reads through the
codebase, so there is exactly one place that defines what's configurable
and what its type/default is. Concurrency caps are deliberately one field
per pipeline stage (not a single shared value) so each stage's rate can be
tuned independently against whatever the upstream service tolerates.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Secrets / third-party credentials.
    hf_token: str
    gmail_address: str
    gmail_app_password: str
    digest_recipients: str

    @field_validator("gmail_app_password")
    @classmethod
    def _strip_app_password_spaces(cls, value: str) -> str:
        # Google's UI displays app passwords as 4 space-separated groups
        # for readability, and pasting it verbatim (spaces included) into
        # an env var is an easy, silent way to end up with a `535 Username
        # and Password not accepted` SMTP auth failure.
        return value.replace(" ", "")

    decodo_username: str
    decodo_password: str

    # Per-stage concurrency caps.
    hn_llm_concurrency: int = Field(default=5, gt=0)
    linkedin_search_concurrency: int = Field(default=10, gt=0)
    linkedin_description_concurrency: int = Field(default=10, gt=0)
    linkedin_llm_concurrency: int = Field(default=16, gt=0)
    # 4 is not an arbitrary choice -- 8 concurrent Bright Data requests were
    # found to trigger "502 Bad Gateway" from Bright Data's own backend
    # (not Indeed) back when Indeed's search went through Bright Data; see
    # indeed_scraper_experiment/DECISIONS.md. Kept as Indeed's search
    # concurrency cap now that it's Selenium-driven too, since running many
    # concurrent headless Chrome instances has its own resource cost.
    indeed_search_concurrency: int = Field(default=4, gt=0)
    indeed_description_concurrency: int = Field(default=4, gt=0)
    indeed_llm_concurrency: int = Field(default=16, gt=0)

    # LinkedIn search pagination.
    linkedin_posts_per_page: int = Field(default=10, gt=0)
    linkedin_max_pages_per_query: int = Field(default=3, gt=0)

    # Indeed search pagination.
    indeed_posts_per_page: int = Field(default=10, gt=0)
    indeed_max_pages_per_query: int = Field(default=4, gt=0)

    # A successful page with fewer cards than this is treated as the last
    # page of real results -- pagination stops rather than trying the next
    # offset. Indeed-only; see sources/indeed/search.py's module docstring.
    indeed_min_cards_per_page: int = Field(default=10, gt=0)

    # How long to wait after loading an Indeed search page (via Selenium)
    # for its client-side JS to finish before reading the page's HTML/URL --
    # in particular, the `vjk` param Indeed's own JS appends to the address
    # bar (see sources/indeed/selenium_client.py) only shows up after this
    # settles, not immediately after navigation.
    indeed_page_settle_seconds: float = Field(default=6, gt=0)

    # Upper bound on collect_jobs() across all sources -- with per-country
    # retries/pagination and a 60s-per-request Decodo timeout (plus
    # Selenium's own page-load/settle time for Indeed search), an unbounded
    # run can otherwise stretch into many minutes with nothing timing it
    # out from inside the app. Doubled from
    # an original 600s: removing Indeed's sc= (Job Type/Experience Level)
    # filter lets more candidates through per country (verified live,
    # Indeed alone: 97 raw cards -> 23 after filter, up from ~7-15 before),
    # needing more time for description-fetch + LLM confirmation.
    digest_run_timeout_seconds: int = Field(default=1200, gt=0)

    def recipient_list(self) -> list[str]:
        recipients = [email.strip() for email in self.digest_recipients.split(",") if email.strip()]
        if not recipients:
            raise ValueError("digest_recipients is set but contains no valid email addresses")
        return recipients


@lru_cache
def get_settings() -> Settings:
    """Cached Settings instance -- constructed once per process."""
    return Settings()
