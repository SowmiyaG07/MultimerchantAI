"""
30 requests at the same time: rate limit + semaphore, everything REAL.

Uses the same real setup as test_load.py (real customers, real JWT,
real Redis rate limit, real LangGraph, real Ollama, real DB reads).

RL-01  1 customer, 30 requests, real limit (CHAT_RATE_LIMIT=100)
       -> all 30 allowed (30 < 100), semaphore holds.
RL-02  1 customer, 30 requests, limit lowered to 10
       -> exactly 10 x 200 and 20 x 429 with Retry-After;
          the 20 blocked requests never reach the LLM or the DB.
RL-03  30 different customers, 1 request each, limit lowered to 10
       -> all 30 allowed: the limit is per customer, not global.

Before each test the customers' rate counters (chat_rate:{id}) are
reset, so earlier runs inside the same 60-second window do not count.

Slow: about 8 seconds per allowed chat with Ollama.
RL-02 ~1.5 min, RL-01 and RL-03 ~4 min each.
"""
from dotenv import load_dotenv

# jwt.py reads .env values at import time, so load .env first.
load_dotenv()

import pytest  # noqa: E402
from redis.exceptions import RedisError  # noqa: E402

# Reuse the real setup from test_load.py (same event loop, same
# customers, same counting wrappers). Only names are imported, so
# pytest does not run test_load.py's tests a second time.
from app.tests.test_load import LOOP, REAL_CUSTOMERS, real_backend, send_chats  # noqa: E402,F401

import app.agent.llm_limits as llm_limits  # noqa: E402
import app.api.dependencies as dependencies  # noqa: E402
import app.cache.redis_cache as redis_cache  # noqa: E402


REQUESTS = 30
LOWERED_LIMIT = 10


async def reset_rate_counters(customers):
    for customer in customers:
        key = f"chat_rate:{customer['customer_id']}"
        redis_cache._local_counters.pop(key, None)
        try:
            await redis_cache.redis_client.delete(key)
        except RedisError:
            pass  # Redis down: the memory fallback counter was cleared above


def run(customers, message="Show my redemption history"):
    LOOP.run_until_complete(reset_rate_counters(customers))
    try:
        return LOOP.run_until_complete(send_chats(customers, message))
    finally:
        LOOP.run_until_complete(reset_rate_counters(customers))


def report(test_id, results, total, tracker, db_calls):
    statuses = [response.status_code for _, response, _ in results]
    ok_times = sorted(d for _, r, d in results if r.status_code == 200)
    blocked_times = sorted(d for _, r, d in results if r.status_code == 429)

    print()
    print("Test:", test_id)
    print("Requests:", len(results))
    print("200 allowed:", statuses.count(200))
    print("429 blocked:", statuses.count(429))
    print("Other statuses:", [s for s in statuses if s not in (200, 429)])
    print("Total time: %.1fs" % total)
    if ok_times:
        print("Allowed  fastest %.1fs  slowest %.1fs" % (ok_times[0], ok_times[-1]))
    if blocked_times:
        print("Blocked  fastest %.2fs  slowest %.2fs" % (blocked_times[0], blocked_times[-1]))
    print("LLM calls:", tracker.calls)
    print("Max parallel LLM calls: %s (limit %s)"
          % (tracker.max_running, llm_limits.LLM_MAX_CONCURRENT_CALLS))
    print("Redemption DB queries:", len(db_calls))

    return statuses


def check_allowed_answers(results):
    for customer, response, _ in results:
        if response.status_code != 200:
            continue
        body = response.json()
        assert body["answer"].strip()
        assert body["session_id"].lower() == customer["external_id"].lower()


# ---------------------------------------------
# RL-01: one customer, 30 at once, real limit
# ---------------------------------------------

def test_rl01_one_customer_under_real_limit(real_backend):
    tracker, db_calls, _ = real_backend

    if dependencies.CHAT_RATE_LIMIT < REQUESTS:
        pytest.skip(f"CHAT_RATE_LIMIT={dependencies.CHAT_RATE_LIMIT} is below {REQUESTS}")

    customers = [REAL_CUSTOMERS[0]] * REQUESTS
    results, total = run(customers)
    statuses = report("RL-01", results, total, tracker, db_calls)

    # 30 < 100: nobody is blocked
    assert statuses.count(200) == REQUESTS, statuses
    check_allowed_answers(results)

    # Every allowed chat used the LLM (1 to 3 calls each)
    assert REQUESTS <= tracker.calls <= 3 * REQUESTS

    # Semaphore: never more than LLM_MAX_CONCURRENT_CALLS at once
    assert tracker.max_running <= llm_limits.LLM_MAX_CONCURRENT_CALLS


# ---------------------------------------------
# RL-02: one customer, 30 at once, limit 10
# ---------------------------------------------

def test_rl02_one_customer_over_limit(real_backend, monkeypatch):
    tracker, db_calls, _ = real_backend
    monkeypatch.setattr(dependencies, "CHAT_RATE_LIMIT", LOWERED_LIMIT)

    customers = [REAL_CUSTOMERS[0]] * REQUESTS
    results, total = run(customers)
    statuses = report("RL-02", results, total, tracker, db_calls)

    # Exactly LOWERED_LIMIT allowed, the rest blocked
    assert statuses.count(200) == LOWERED_LIMIT, statuses
    assert statuses.count(429) == REQUESTS - LOWERED_LIMIT, statuses
    check_allowed_answers(results)

    # Every 429 has the right body and a Retry-After inside the window
    for _, response, _ in results:
        if response.status_code == 429:
            detail = response.json()["detail"]
            assert detail["code"] == "RATE_LIMITED"
            retry_after = int(response.headers["Retry-After"])
            assert 1 <= retry_after <= dependencies.CHAT_RATE_WINDOW
            assert detail["retry_after_seconds"] == retry_after

    # Blocked requests never reached the LLM: only the 10 allowed chats
    # made LLM calls (1 to 3 each)
    assert LOWERED_LIMIT <= tracker.calls <= 3 * LOWERED_LIMIT, tracker.calls

    # ...and never reached the redemption database
    assert len(db_calls) <= LOWERED_LIMIT

    assert tracker.max_running <= llm_limits.LLM_MAX_CONCURRENT_CALLS


# ---------------------------------------------
# RL-03: 30 different customers, limit 10
# ---------------------------------------------

def test_rl03_thirty_customers_limit_is_per_customer(real_backend, monkeypatch):
    tracker, db_calls, _ = real_backend
    monkeypatch.setattr(dependencies, "CHAT_RATE_LIMIT", LOWERED_LIMIT)

    if len(REAL_CUSTOMERS) < REQUESTS:
        pytest.skip(f"needs {REQUESTS} customers, database has {len(REAL_CUSTOMERS)}")

    customers = REAL_CUSTOMERS[:REQUESTS]
    results, total = run(customers)
    statuses = report("RL-03", results, total, tracker, db_calls)

    # 1 request per customer is far under 10: nobody is blocked,
    # so the limit is NOT shared between customers
    assert statuses.count(200) == REQUESTS, statuses
    check_allowed_answers(results)

    assert REQUESTS <= tracker.calls <= 3 * REQUESTS
    assert tracker.max_running <= llm_limits.LLM_MAX_CONCURRENT_CALLS
