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

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Secrets / third-party credentials.
    gmail_address: str
    gmail_app_password: str
    digest_recipients: str

    # Sponsorship-confirmation LLM, served locally via Docker Model Runner's
    # OpenAI-compatible API (see shared/visa_llm.py) -- replaced the
    # Hugging Face Inference Providers router this used to call, to remove
    # the per-call cost/rate-limit and keep confirmation entirely local; see
    # DECISIONS.md. Only resolves on a machine actually running Docker Model
    # Runner with this model loaded (the same Mac scripts/run_digest_locally.sh
    # already runs the whole digest on), hence overridable rather than
    # hardcoded.
    llm_base_url: str = Field(default="http://localhost:12434/engines/llama.cpp/v1")
    llm_model: str = Field(default="hf.co/unsloth/qwen3-4b-instruct-2507-gguf")

    @field_validator("gmail_app_password")
    @classmethod
    def _strip_app_password_spaces(cls, value: str) -> str:
        # Google's UI displays app passwords as 4 space-separated groups
        # for readability, and pasting it verbatim (spaces included) into
        # an env var is an easy, silent way to end up with a `535 Username
        # and Password not accepted` SMTP auth failure.
        return value.replace(" ", "")

    # Used for LinkedIn's search + description fetches, and Indeed's
    # description fetches (Indeed search stays on Selenium) -- Decodo used
    # to serve LinkedIn and Bright Data used to serve Indeed's description
    # fetches; both now go through Bright Data's Web Unlocker, since a live
    # comparison already found Bright Data more reliable than Decodo for
    # Indeed (401s and read timeouts), and there's no reason to keep two
    # scraping providers around otherwise. See DECISIONS.md.
    brightdata_api_key: str
    brightdata_zone: str

    # Residential proxy for Indeed's Selenium-driven *search* fetches
    # (unlike the settings above, this doesn't touch description fetches).
    # All four unset (the default) means Selenium connects directly,
    # unproxied -- the original setup, which only works from a
    # residential/office IP (this machine's own). Real username/password
    # auth, not an IP whitelist -- switching from Chrome to Firefox (see
    # selenium_client.py) made a real proxy-auth WebExtension workable
    # again, which is what makes credentials-based auth worth having:
    # it doesn't depend on a specific machine's IP staying whitelisted
    # with the proxy provider. Deliberately no per-request country
    # targeting -- see selenium_client.py's docstring for why.
    indeed_selenium_proxy_host: str | None = None
    indeed_selenium_proxy_port: int | None = None
    indeed_selenium_proxy_username: str | None = None
    indeed_selenium_proxy_password: str | None = None

    @model_validator(mode="after")
    def _proxy_settings_are_all_or_nothing(self) -> Settings:
        # A partially-set proxy config (e.g. host set but password missing)
        # would otherwise surface as a confusing failure deep inside
        # selenium_client.py the next time Indeed search runs, rather than
        # a clear error at startup.
        proxy_fields = (
            self.indeed_selenium_proxy_host,
            self.indeed_selenium_proxy_port,
            self.indeed_selenium_proxy_username,
            self.indeed_selenium_proxy_password,
        )
        if any(field is not None for field in proxy_fields) and not all(field is not None for field in proxy_fields):
            raise ValueError(
                "indeed_selenium_proxy_host/port/username/password must be all set or all unset -- "
                f"got {proxy_fields!r}"
            )
        return self

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

    # Delay before every sequential Selenium fetch for the same Indeed
    # country/query after the first -- both between pagination pages and
    # between retry attempts on the same page (see sources/indeed/search.py).
    # Each fetch already gets a fresh rotating residential-proxy IP with no
    # shared cookies, but `vjk` (see search.py's module docstring) is a
    # correlating token carried across every page of one query regardless --
    # the same session identifier suddenly arriving from a different exit
    # IP on every request, with no gap at all, is a plausible bot signal
    # independent of IP reputation. Not yet confirmed to reduce block rates
    # (see DECISIONS.md); a cheap, low-risk experiment.
    indeed_sequential_call_delay_seconds: float = Field(default=2, ge=0)

    # Upper bound on collect_jobs() across all sources -- with per-country
    # retries/pagination and a 60s-per-request Bright Data timeout (plus
    # Selenium's own page-load/settle time for Indeed search), an unbounded
    # run can otherwise stretch into many minutes with nothing timing it
    # out from inside the app. Doubled from an original 600s to 1200s:
    # removing Indeed's sc= (Job Type/Experience Level) filter lets more
    # candidates through per country (verified live, Indeed alone: 97 raw
    # cards -> 23 after filter, up from ~7-15 before), needing more time for
    # description-fetch + LLM confirmation. Doubled again to 2400s after
    # two consecutive real local runs (2026-08-11) both hit the 1200s cap
    # mid-run -- LinkedIn's own candidate volume across its 17 countries
    # (670+) now regularly reaches its LLM-confirmation stage only with the
    # budget already exhausted, sometimes compounded by Indeed's bot-block
    # retry/backoff eating wall-clock time concurrently. See DECISIONS.md.
    digest_run_timeout_seconds: int = Field(default=2400, gt=0)

    def recipient_list(self) -> list[str]:
        recipients = [email.strip() for email in self.digest_recipients.split(",") if email.strip()]
        if not recipients:
            raise ValueError("digest_recipients is set but contains no valid email addresses")
        return recipients


@lru_cache
def get_settings() -> Settings:
    """Cached Settings instance -- constructed once per process."""
    return Settings()
