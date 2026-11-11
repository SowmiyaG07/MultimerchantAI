import asyncio
import os
import time

from app.utilities.log import Applogger
from app.utilities.server_settings import WORKER_COUNT, per_worker


from dotenv import load_dotenv

from app.utilities.log import Applogger

load_dotenv()

logger = Applogger.get_loggerdata(__name__)

# LLM_MAX_CONCURRENT_CALLS in .env is the limit for the WHOLE SERVER.
# Every worker process has its own semaphore, so each one gets an
# equal share: 2 workers x 1 slot = 2 LLM calls at once in total.
LLM_TOTAL_CONCURRENT_CALLS = int(os.getenv("LLM_MAX_CONCURRENT_CALLS", "4"))

# Slots for THIS worker process (the semaphore size).
LLM_MAX_CONCURRENT_CALLS = per_worker(LLM_TOTAL_CONCURRENT_CALLS)

# Real server total: more than configured when the total is smaller
# than the worker count or not divisible by it (every worker needs 1).
LLM_EFFECTIVE_TOTAL_CALLS = LLM_MAX_CONCURRENT_CALLS * WORKER_COUNT

if LLM_EFFECTIVE_TOTAL_CALLS != LLM_TOTAL_CONCURRENT_CALLS:
    logger.warning(
        "llm_concurrency_adjusted configured_total=%s workers=%s "
        "per_worker=%s effective_total=%s",
        LLM_TOTAL_CONCURRENT_CALLS, WORKER_COUNT,
        LLM_MAX_CONCURRENT_CALLS, LLM_EFFECTIVE_TOTAL_CALLS,
    )

LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))

# How long a call may WAIT for a free slot before we give up.
# Without this, calls queue forever when the LLM is overloaded.
LLM_QUEUE_TIMEOUT_SECONDS = float(os.getenv("LLM_QUEUE_TIMEOUT_SECONDS", "20"))

# One semaphore for the whole Python process: at most
# LLM_MAX_CONCURRENT_CALLS LLM calls run at the same time.
# Extra calls wait here until a slot is free.
llm_semaphore = asyncio.Semaphore(LLM_MAX_CONCURRENT_CALLS)


class LLMBusyError(Exception):
    """No LLM slot became free within LLM_QUEUE_TIMEOUT_SECONDS."""


async def call_llm(runnable, prompt, name: str):
    """
    Run runnable.ainvoke(prompt) with the concurrency limit and timeouts.

    - Waits at most LLM_QUEUE_TIMEOUT_SECONDS for a free slot,
      otherwise raises LLMBusyError (the API returns 503).
    - Cancels the call after LLM_TIMEOUT_SECONDS and raises TimeoutError.
    - The slot is always released: success, error or timeout.
    """
    wait_start = time.perf_counter()

    try:
        await asyncio.wait_for(
            llm_semaphore.acquire(),
            timeout=LLM_QUEUE_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "llm_busy name=%s waited_ms=%.0f queue_timeout_seconds=%s",
            name, (time.perf_counter() - wait_start) * 1000,
            LLM_QUEUE_TIMEOUT_SECONDS,
        )
        raise LLMBusyError(f"No free LLM slot within {LLM_QUEUE_TIMEOUT_SECONDS}s") from None

    waited_ms = (time.perf_counter() - wait_start) * 1000
    start = time.perf_counter()

    try:
        return await asyncio.wait_for(
            runnable.ainvoke(prompt),
            timeout=LLM_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "llm_timeout name=%s timeout_seconds=%s",
            name, LLM_TIMEOUT_SECONDS,
        )
        raise
    finally:
        llm_semaphore.release()
        logger.info(
            "llm_call name=%s waited_ms=%.0f duration_ms=%.0f",
            name, waited_ms, (time.perf_counter() - start) * 1000,
        )
