"""
Server process settings shared by every module.

Uvicorn runs WEB_CONCURRENCY worker processes (it also uses this
variable as the default for --workers). Each worker is a separate
Python process with its own memory, so in-memory limits (the LLM
semaphore, the Redis-down rate-limit fallback) are counted per
worker. They are divided by the worker count so the SERVER total
stays at the configured value.
"""

import math
import os

from dotenv import load_dotenv

load_dotenv()


def _read_worker_count() -> int:
    raw = os.getenv("WEB_CONCURRENCY", "1").strip()
    try:
        workers = int(raw)
    except ValueError:
        raise RuntimeError("WEB_CONCURRENCY must be a whole number of workers") from None
    if workers < 1:
        raise RuntimeError("WEB_CONCURRENCY must be at least 1")
    return workers


WORKER_COUNT = _read_worker_count()


def per_worker(total: int) -> int:
    """
    Share of a server-wide limit for one worker, at least 1.

    Rounded up, so the server never allows LESS than `total`
    (e.g. 5 over 2 workers -> 3 each, 6 in total).
    """
    return max(1, math.ceil(total / WORKER_COUNT))