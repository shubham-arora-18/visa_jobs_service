"""Entry point for the scheduled digest run (no HTTP server needed).

Calls the exact same service-layer run_digest used by the FastAPI
POST /digest/run endpoint, so the CLI and the API are always in sync --
there's exactly one implementation of "what a run does". Defaults match
DigestRunRequest's own defaults (default LinkedIn keywords, a 1-day
window), since a daily scheduled digest is the "day" use case.

Any failure is reported via a failure email inside run_digest itself
(see api.services.digest_service), then re-raised here so the process
exits non-zero and a scheduled CI run is visibly marked failed.
"""

from __future__ import annotations

import asyncio
import logging

from visa_jobs_api.api.dto.digest import DigestRunRequest
from visa_jobs_api.api.services.digest_service import run_digest
from visa_jobs_api.config import get_settings


async def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    request = DigestRunRequest()
    await run_digest(
        settings=settings,
        linkedin_keywords=request.linkedin_keywords,
        posted_within_hours=request.posted_within_hours(),
        posted_within_label=request.posted_within_label(),
    )


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
