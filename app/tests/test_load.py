"""
Load tests with everything REAL.

N different REAL customers (active, with an ExternalID, read from the
database) send /api/v1/chat at the same time through:
- the real FastAPI app and JWT check (a real token per customer)
- the real rate limit (Redis, with its memory fallback)
- the real customer lookup in chat.py
- the real LangGraph graph and real supervisor/response LLM calls
- the real semaphore and timeout
- the real merchant, offer and redemption database reads
- the real chat history loading (Redis, then SQL)

Not done by default:
- Saving the test chats into the customers' real history (SQL and
  Redis). Set LOAD_TEST_SAVE_HISTORY=1 to save anyway.
- Google Maps: no latitude/longitude is sent, so nearby questions get
  "location required".

Answers are NOT printed, because they contain real customer data.

Real LLM calls are slow: Ollama runs about 11-12 calls per minute,
so 100 users take about 17 minutes. Run selected cases with -k.
"""


import asyncio
import json
import os
import time
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

import app.agent.llm_limits as llm_limits
import app.agent.munomultiagent as munomultiagent
import app.agent.redemption_agent as redemption_agent
import app.agent.response_agent as response_agent
import app.api.route.chat as chat_route
from app.api.jwt import create_access_token
from app.api.main import app
from app.database.database import get_connection


SAVE_HISTORY = os.getenv("LOAD_TEST_SAVE_HISTORY") == "1"


DATASET_PATH = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "evaluation"
    / "datasets"
    / "load_tests.json"
)


with open(DATASET_PATH, "r", encoding="utf-8") as file:
    TEST_CASES = json.load(file)

# One event loop for all cases: the real LLM client keeps its HTTP
# connections on the loop it first ran on.
LOOP = asyncio.new_event_loop()

# Real customers from the database (read-only)
# ---------------------------------------------

def load_real_customers() -> list[dict]:
    """Active customers that can chat (they need an ExternalID)."""
    connection = get_connection()
    db = next(connection)
    try:
        rows = db.execute(text("""
            SELECT CustomerID, FirstName, LastName, WhiteLabelID, ExternalID
            FROM dbo.ALL_Get1CardCustomer
            WHERE Active = 1 AND ExternalID IS NOT NULL
            ORDER BY CustomerID
        """)).mappings().all()
    finally:
        connection.close()

    return [
        {
            "customer_id": row["CustomerID"],
            "first_name": row["FirstName"] or "",
            "last_name": row["LastName"] or "",
            "email": "",
            "whitelabel_id": row["WhiteLabelID"],
            "external_id": str(row["ExternalID"]),
        }
        for row in rows
    ]


REAL_CUSTOMERS = load_real_customers()


class LLMTracker:
    """Counts real LLM calls and how many run at the same time."""

    def __init__(self):
        self.running = 0
        self.max_running = 0
        self.calls = 0
        self.durations = []


class TrackedLLM:
    """Passes every call to the real LLM and records it."""

    def __init__(self, real_llm, tracker):
        self.real_llm = real_llm
        self.tracker = tracker

    async def ainvoke(self, prompt):
        tracker = self.tracker
        tracker.running += 1
        tracker.calls += 1
        tracker.max_running = max(tracker.max_running, tracker.running)
        start = time.perf_counter()
        try:

            result = await self.real_llm.ainvoke(prompt)
            return result

        finally:
            end = time.perf_counter()
            duration = end - start

            tracker.durations.append(duration)
            tracker.running = tracker.running - 1


@pytest.fixture
def real_backend(monkeypatch):
    tracker = LLMTracker()
    db_calls = []
    merchants_by_customer = {}

    # Fresh semaphore for each test case
    monkeypatch.setattr(
        llm_limits, "llm_semaphore",
        asyncio.Semaphore(llm_limits.LLM_MAX_CONCURRENT_CALLS),
    )

    # Real LLM, counted
    monkeypatch.setattr(munomultiagent, "supervised_llm",
                        TrackedLLM(munomultiagent.supervised_llm, tracker))

    monkeypatch.setattr(
        response_agent, "llm",
        TrackedLLM(response_agent.llm, tracker),
    )

    # Real redemption database, recorded
    real_get_redemption_history = redemption_agent.get_redemption_history

    async def recorded_redemptions(customer_id, from_date, to_date, merchant_name=None):
        db_calls.append(str(customer_id))
        records = await real_get_redemption_history(customer_id, from_date, to_date, merchant_name)

        names = set()
        for record in records:
            name = (record.get("DBAName") or record.get("BusinessName") or "").strip()
            if name:
                names.add(name)
        merchants_by_customer[str(customer_id)] = names

        return records

    monkeypatch.setattr(redemption_agent, "get_redemption_history", recorded_redemptions)

    # Do not write test chats into real customers' history
    if not SAVE_HISTORY:
        async def none(*args, **kwargs):
            return None

        monkeypatch.setattr(chat_route, "save_conversation_to_sql", none)
        monkeypatch.setattr(chat_route, "append_conversation_to_redis", none)

    return tracker, db_calls, merchants_by_customer


async def send_chats(customers, message):

    # Create one real token per customer
    tokens = {}
    for customer in customers:
        tokens[customer["customer_id"]] = create_access_token(customer)

    # Create HTTP client
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        timeout=None
    ) as client:

        # Send request for one customer
        async def one(customer):

            start = time.perf_counter()

            customer_id = customer["customer_id"]
            token = tokens[customer_id]

            response = await client.post(
                "/api/v1/chat",
                json={"message": message},
                headers={"Authorization": f"Bearer {token}"}
            )

            duration = time.perf_counter() - start

            return customer, response, duration

        # Prepare all requests
        tasks = []

        for customer in customers:
            tasks.append(one(customer))

        # Run requests concurrently
        start = time.perf_counter()

        results = await asyncio.gather(*tasks)

        total = time.perf_counter() - start

    return results, total



@pytest.mark.parametrize("test_case", TEST_CASES, ids=lambda case: case["id"])
def test_concurrent_customers_real(test_case, real_backend):

    # Step 1: Get tracking information
    tracker, db_calls, merchants_by_customer = real_backend

    test_id = test_case["id"]
    user_count = test_case["concurrent_users"]
    expected = test_case["expected"]

    # Step 2: Check available customers
    if user_count > len(REAL_CUSTOMERS):
        pytest.skip("Not enough customers")

    customers = REAL_CUSTOMERS[:user_count]

    # Step 3: Send concurrent chat requests
    results, total = LOOP.run_until_complete(
        send_chats(customers, test_case["message"])
    )

    # Step 4: Collect status codes and durations
    statuses = []
    latencies = []

    for customer, response, duration in results:
        statuses.append(response.status_code)
        latencies.append(duration)

    latencies.sort()

    # Step 5: Calculate API performance
    average = sum(latencies) / len(latencies)
    maximum = max(latencies)

    # Step 6: Calculate LLM performance
    if tracker.durations:
        llm_avg = sum(tracker.durations) / len(tracker.durations)
        llm_max = max(tracker.durations)
    else:
        llm_avg = 0
        llm_max = 0

    print("Test:", test_id)
    print("Customers:", user_count)
    print("Total time:", total)
    print("Average API time:", average)
    print("Maximum API time:", maximum)
    print("LLM calls:", tracker.calls)
    print("Average LLM time:", llm_avg)
    print("Maximum LLM time:", llm_max)

    # Step 7: Check successful responses
    success_count = statuses.count(expected["status_code"])

    assert success_count == expected["success_count"], f"{test_id} statuses: {statuses}"

    # Step 8: Check each customer response
    for customer, response, duration in results:

        body = response.json()
        customer_id = str(customer["customer_id"])

        assert body["answer"].strip(), f"{test_id} empty answer for {customer_id}"

        assert body["answer"] != munomultiagent.SUPERVISOR_ERROR_MESSAGE, (
            f"{test_id} LLM failed or timed out for {customer_id}"
        )

        # Step 9: Check customer data isolation
        if expected["no_data_mixing"]:

            assert body["session_id"].lower() == customer["external_id"].lower(), (
                f"{test_id} session mixed for {customer_id}"
            )

            own = merchants_by_customer.get(customer_id, set())

            others = set()

            for cid, names in merchants_by_customer.items():
                if cid != customer_id:
                    others.update(names)

            others = others - own

            leaked = []

            for name in others:
                if name.lower() in body["answer"].lower():
                    leaked.append(name)

            assert not leaked, f"{test_id} answer for {customer_id} names another customer's merchant"

    # Step 10: Check redemption database customer IDs
    if expected["no_data_mixing"]:

        callers = set()

        for customer, response, duration in results:
            callers.add(str(customer["customer_id"]))

        assert set(db_calls) <= callers
        assert len(db_calls) == len(set(db_calls))

    # Step 11: Check unique trace IDs
    if expected["unique_trace_ids"]:

        trace_ids = set()

        for customer, response, duration in results:
            trace_ids.add(response.json()["trace_id"])

        assert len(trace_ids) == user_count

    # Step 12: Check LLM call count
    assert user_count <= tracker.calls <= 3 * user_count, f"{test_id} LLM calls: {tracker.calls}"

    # Step 13: Check semaphore limit
    if expected["max_parallel_llm_within_limit"]:

        assert tracker.max_running <= llm_limits.LLM_MAX_CONCURRENT_CALLS
