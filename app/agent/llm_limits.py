import asyncio
import os
import time

from dotenv import load_dotenv

from app.utilities.log import Applogger

load_dotenv()

logger = Applogger.get_loggerdata(__name__)

LLM_MAX_CONCURRENT_CALLS = int(os.getenv("LLM_MAX_CONCURRENT_CALLS", "4"))
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))

# One semaphore for the whole Python process: at most
# LLM_MAX_CONCURRENT_CALLS LLM calls run at the same time.
# Extra calls wait here until a slot is free.
llm_semaphore = asyncio.Semaphore(LLM_MAX_CONCURRENT_CALLS)


async def call_llm(runnable, prompt, name: str):
    """
    Run runnable.ainvoke(prompt) with the concurrency limit and timeout.

    - Waits for a free slot (max LLM_MAX_CONCURRENT_CALLS at once).
    - Cancels the call after LLM_TIMEOUT_SECONDS and raises TimeoutError.
    - The slot is always released: success, error or timeout.
    """
    async with llm_semaphore:
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
            logger.info(
                "llm_call name=%s duration_ms=%.0f",
                name, (time.perf_counter() - start) * 1000,
            )
