"""
Failed answers must not be saved as successful chat history.

- Any failure (LLM, DB, Google) -> saved with status="error" and the
  failure codes in error_message, and NOT appended to Redis.
- Success -> status="success" and appended to Redis.
- The SQL history loader returns only status='success' rows.

Fake LLM and tools, so no Ollama is needed. The last test reads the
real ConversationHistory table (read-only).
"""
from dotenv import load_dotenv

load_dotenv()

import asyncio  # noqa: E402
from collections import defaultdict  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

import app.agent.llm_limits as llm_limits  # noqa: E402
import app.agent.merchant_agent as merchant_agent  # noqa: E402
import app.agent.munomultiagent as munomultiagent  # noqa: E402
import app.agent.offer_agent as offer_agent  # noqa: E402
import app.agent.response_agent as response_agent  # noqa: E402
import app.api.dependencies as dependencies  # noqa: E402
import app.api.route.chat as chat_route  # noqa: E402
from app.api.jwt import create_access_token  # noqa: E402
from app.api.main import app  # noqa: E402
from app.database.database import get_connection  # noqa: E402
from app.services.history_service import load_relevant_history_from_sql  # noqa: E402


def decision(**overrides):
    data = {
        "business_related": True,
        "history_needed": False,
        "other_customer_requested": False,
        "selected_agent": ["merchant_agent"],
        "rewritten_query": "Dreamland BBQ details",
        "merchant_name": "Dreamland BBQ",
        "merchant_search_params": [{"name": "Dreamland BBQ"}],
    }
    data.update(overrides)
    return munomultiagent.SupervisorDecision.model_validate(data)


class FakeLLM:
    def __init__(self, result=None, fail=False):
        self.result = result
        self.fail = fail

    async def ainvoke(self, prompt):
        if self.fail:
            raise ConnectionError("Ollama is unavailable")
        return self.result


class Msg:
    def __init__(self, content):
        self.content = content


@pytest.fixture
def backend(monkeypatch):
    saved = []
    appended = []

    monkeypatch.setattr(llm_limits, "llm_semaphore", asyncio.Semaphore(2))

    def fake_customer(customer_id):
        return {"active": True, "external_id": f"EXT-{customer_id}"}

    async def fake_save(**kwargs):
        saved.append(kwargs)

    async def fake_append(**kwargs):
        appended.append(kwargs)

    async def allowed(*a, **k):
        return 0

    async def one_merchant(**kwargs):
        return [defaultdict(lambda: None, {"LocationID": 1, "DBAName": "Dreamland BBQ", "BusinessName": None,
                 "Address": "5535 15th Ave", "City": "Tuscaloosa", "State": "AL",
                 "Zip": "35405", "ContactPhone": "205-555-0100"})]

    monkeypatch.setattr(chat_route, "getcustomerid", fake_customer)
    monkeypatch.setattr(chat_route, "save_conversation_to_sql", fake_save)
    monkeypatch.setattr(chat_route, "append_conversation_to_redis", fake_append)
    monkeypatch.setattr(dependencies, "check_rate_limit", allowed)
    monkeypatch.setattr(merchant_agent, "searchmerchant", one_merchant)
    monkeypatch.setattr(response_agent, "llm", FakeLLM(Msg("Dreamland BBQ is at 5535 15th Ave.")))

    return saved, appended


def chat(message="Tell me about Dreamland BBQ"):
    async def main():
        token = create_access_token({"customer_id": 90001, "first_name": "T", "last_name": "T"})
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30) as client:
            return await client.post("/api/v1/chat", json={"message": message},
                                     headers={"Authorization": f"Bearer {token}"})
    return asyncio.run(main())


def test_success_is_saved_and_cached(backend, monkeypatch):
    saved, appended = backend
    monkeypatch.setattr(munomultiagent, "supervised_llm", FakeLLM(decision()))

    response = chat()

    assert response.status_code == 200
    assert saved[0]["status"] == "success"
    assert saved[0]["error_message"] is None
    assert len(appended) == 1


def test_supervisor_llm_failure_is_saved_as_error_not_cached(backend, monkeypatch):
    saved, appended = backend
    monkeypatch.setattr(munomultiagent, "supervised_llm", FakeLLM(fail=True))

    response = chat("Show my redemption history")

    assert response.status_code == 200
    assert response.json()["answer"] == munomultiagent.SUPERVISOR_ERROR_MESSAGE
    assert saved[0]["status"] == "error"
    assert saved[0]["error_message"] == "supervisor_llm_failed"
    assert appended == []                      # not in Redis history


def test_response_llm_failure_is_saved_as_error(backend, monkeypatch):
    saved, appended = backend
    monkeypatch.setattr(munomultiagent, "supervised_llm", FakeLLM(decision()))
    monkeypatch.setattr(response_agent, "llm", FakeLLM(fail=True))

    response = chat()

    assert response.status_code == 200
    assert saved[0]["status"] == "error"
    assert saved[0]["error_message"] == "response_llm_failed"
    assert appended == []


def test_two_parallel_agents_fail_together(backend, monkeypatch):
    """Both agents write "errors" in the same step: needs the reducer."""
    saved, appended = backend
    monkeypatch.setattr(munomultiagent, "supervised_llm", FakeLLM(decision(
        selected_agent=["merchant_agent", "offer_agent"],
        offer_search_params=[{"name": "Dreamland BBQ"}],
    )))

    async def db_down(**kwargs):
        raise ConnectionError("SQL Server unavailable")

    monkeypatch.setattr(merchant_agent, "searchmerchant", db_down)
    monkeypatch.setattr(offer_agent, "searchoffer_data", db_down)

    response = chat("Dreamland BBQ details and offers")

    assert response.status_code == 200          # no InvalidUpdateError -> no 500
    assert saved[0]["status"] == "error"
    assert sorted(saved[0]["error_message"].split(",")) == [
        "merchant_search_failed", "offer_search_failed",
    ]
    assert appended == []


def test_sql_history_loader_returns_only_success_rows():
    """Real table, read-only: the loader returns exactly the success rows."""
    connection = get_connection()
    db = next(connection)
    try:
        session_id = db.execute(text("""
            SELECT TOP 1 session_id FROM dbo.ConversationHistory
            WHERE answer IS NOT NULL
            GROUP BY session_id
            ORDER BY COUNT(DISTINCT status) DESC, COUNT(*) DESC
        """)).scalar()
        expected = db.execute(text("""
            SELECT COUNT(*) FROM dbo.ConversationHistory
            WHERE session_id = :s AND answer IS NOT NULL AND status = 'success'
        """), {"s": session_id}).scalar()
    finally:
        connection.close()

    if not session_id:
        pytest.skip("ConversationHistory is empty")

    history = asyncio.run(load_relevant_history_from_sql(
        session_id, "x", max_tokens=10**9, max_turns=10**6,
    ))
    assert len(history) == 2 * expected      # question + answer per success row
