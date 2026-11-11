import asyncio
import os
import time

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.agent.llm_provider import get_llm_model_name, get_llm_provider
from app.cache.redis_cache import redis_client
from app.database.database import engine
from app.utilities.log import Applogger

logger = Applogger.get_loggerdata(__name__)

# No prefix here: main.py mounts this router at /api/v1/health.
# (A "/health" prefix here as well gave /api/v1/health/health/.)
healthrouter = APIRouter(tags=["health"])

# Each dependency gets at most this long, so /ready answers quickly
# even when something is down.
CHECK_TIMEOUT_SECONDS = float(os.getenv("HEALTH_CHECK_TIMEOUT_SECONDS", "3"))


# ---------------------------------------------
# GET /api/v1/health  -> liveness
# ---------------------------------------------

# "" instead of "/" so the URL is exactly /api/v1/health, without a
# 307 redirect to /api/v1/health/ (load balancers often don't follow it).
@healthrouter.get("")
def health_check():
    """The process is running. Checks nothing else, so it stays fast."""
    return {"status" : "Healthy"}


# ---------------------------------------------
# GET /api/v1/health/ready  -> readiness
# ---------------------------------------------

def _sql_select_one() -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


async def _check_sql() -> None:
    await asyncio.to_thread(_sql_select_one)


async def _check_redis() -> None:
    await redis_client.ping()


async def _check_llm() -> None:
    if get_llm_provider() == "gemini":
        await _check_gemini()
    else:
        await _check_ollama()


async def _check_gemini() -> None:
    """Fetches the model's metadata: checks key and model without generating."""
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY is not set")

    async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
        response = await client.get(
            f"https://generativelanguage.googleapis.com/v1beta/models/{get_llm_model_name()}",
            # Key in a header, not the URL, so it never lands in access logs.
            headers={"x-goog-api-key": api_key},
        )
        response.raise_for_status()


async def _check_ollama() -> None:
    """Ollama answers /api/version without loading the model."""
    base_url = (os.getenv("OLLAMA_BASE_URL") or "").rstrip("/")
    if not base_url:
        raise RuntimeError("OLLAMA_BASE_URL is not set")

    async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
        response = await client.get(f"{base_url}/api/version")
        response.raise_for_status()


async def _run_check(name: str, check) -> dict:
    start = time.perf_counter()
    try:
        await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_SECONDS)
        status = "ok"
    except Exception as error:
        status = "down"
        # Only the error type: messages can contain host names or URLs.
        logger.warning("health_check_failed check=%s error=%s", name, type(error).__name__)

    return {
        "status": status,
        "duration_ms": round((time.perf_counter() - start) * 1000),
    }


# SQL and the LLM are required: without them no chat can be answered.
# Redis is optional: rate limit and history fall back without it.
REQUIRED_CHECKS = {"sql": _check_sql, "llm": _check_llm}
OPTIONAL_CHECKS = {"redis": _check_redis}


@healthrouter.get("/ready")
async def readiness_check():
    """
    Can this instance answer chats right now?

    200 "Ready"    - SQL and LLM are up (Redis may be down: "Degraded")
    503 "NotReady" - SQL or LLM is down; the load balancer should stop
                     sending traffic here until it recovers.
    """
    all_checks = {**REQUIRED_CHECKS, **OPTIONAL_CHECKS}

    # All checks at the same time, so the answer takes at most one timeout
    results = await asyncio.gather(
        *[_run_check(name, check) for name, check in all_checks.items()]
    )
    checks = dict(zip(all_checks, results))

    required_ok = all(checks[name]["status"] == "ok" for name in REQUIRED_CHECKS)
    optional_ok = all(checks[name]["status"] == "ok" for name in OPTIONAL_CHECKS)

    if not required_ok:
        status, code = "NotReady", 503
    elif not optional_ok:
        status, code = "Degraded", 200
    else:
        status, code = "Ready", 200

    return JSONResponse(status_code=code, content={"status": status, "checks": checks})
