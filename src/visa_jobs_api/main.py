"""FastAPI application factory and entry point (`uvicorn visa_jobs_api.main:app`)."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from visa_jobs_api.api.routers.digest import router as digest_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(title="Visa Jobs API", version="0.1.0")
app.include_router(digest_router)
