"""HTTP routes for triggering and checking the digest pipeline.

This layer only translates HTTP <-> DTOs; all actual orchestration lives in
api.services.digest_service.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from visa_jobs_api.api.dto.digest import DigestRunRequest, DigestRunResponse
from visa_jobs_api.api.services.digest_service import run_digest
from visa_jobs_api.config import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/digest/run", response_model=DigestRunResponse)
async def trigger_digest_run(
    request: DigestRunRequest = DigestRunRequest(), settings: Settings = Depends(get_settings)
) -> DigestRunResponse:
    # This is the one deliberate broad exception catch in the API layer:
    # it's the HTTP boundary, so any failure from the service layer (which
    # has already logged it and sent a failure-notification email) must
    # still produce a well-formed HTTP error response rather than an
    # unhandled-exception 500 with no detail.
    try:
        return await run_digest(
            settings=settings,
            linkedin_keywords=request.linkedin_keywords,
            posted_within_hours=request.posted_within_hours(),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"digest run failed: {exc}") from exc
