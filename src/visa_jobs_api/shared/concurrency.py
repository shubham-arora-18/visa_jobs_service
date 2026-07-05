"""Bounded-concurrency helper shared by every pipeline stage.

Each stage (LinkedIn search pages, LinkedIn description fetches, either
source's LLM confirmation calls) needs its own independently-tunable cap on
how many tasks run at once, to stay under whatever rate the upstream
service (LinkedIn/Bright Data, Hugging Face) tolerates -- see
Settings.*_concurrency. This is the one place that implements "run these,
but at most N at a time."
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


async def gather_limited(items: Sequence[T], worker: Callable[[T], Awaitable[R]], *, limit: int) -> list[R]:
    """Run worker(item) for every item, at most `limit` concurrently.

    Preserves input order in the result list. A worker's exception
    propagates immediately (asyncio.gather's default fail-fast behavior) --
    it is not caught or converted into a partial result here.
    """
    semaphore = asyncio.Semaphore(limit)

    async def _run(item: T) -> R:
        async with semaphore:
            return await worker(item)

    return await asyncio.gather(*(_run(item) for item in items))
