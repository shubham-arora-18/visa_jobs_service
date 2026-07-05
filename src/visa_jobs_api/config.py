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

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Secrets / third-party credentials.
    hf_token: str
    gmail_address: str
    gmail_app_password: str
    digest_recipients: str

    brightdata_api_key: str
    brightdata_zone: str

    # Per-stage concurrency caps.
    hn_llm_concurrency: int = Field(default=5, gt=0)
    linkedin_search_concurrency: int = Field(default=10, gt=0)
    linkedin_description_concurrency: int = Field(default=10, gt=0)
    linkedin_llm_concurrency: int = Field(default=16, gt=0)

    # LinkedIn search pagination.
    linkedin_posts_per_page: int = Field(default=10, gt=0)
    linkedin_max_pages_per_query: int = Field(default=3, gt=0)

    def recipient_list(self) -> list[str]:
        recipients = [email.strip() for email in self.digest_recipients.split(",") if email.strip()]
        if not recipients:
            raise ValueError("digest_recipients is set but contains no valid email addresses")
        return recipients


@lru_cache
def get_settings() -> Settings:
    """Cached Settings instance -- constructed once per process."""
    return Settings()
