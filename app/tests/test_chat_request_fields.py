"""
/chat takes the customer only from the JWT. customer_id / session_id
in the request body are not part of the schema and are ignored, so a
caller cannot name another customer or session.
Fake LLM and customer lookup: no Ollama, DB or Redis needed.
"""
from dotenv import load_dotenv

load_dotenv()

import asyncio  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.agent.llm_limits as llm_limits  # noqa: E402
import app.agent.munomultiagent as munomultiagent  # noqa: E402
import app.api.dependencies as dependencies  # noqa: E402
import app.api.route.chat as chat_route  # noqa: E402
from app.api.jwt import create_access_token  # noqa: E402
from app.api.main import app  # noqa: E402
from app.api.schema.chatrequest import chatrequest  # noqa: E402


class FakeSupervisor:
    async def ainvoke(self, prompt):
        return munomultiagent.SupervisorDecision.model_validate({
            "business_related": False, "history_needed": False,
            "other_customer_requested": False, "selected_agent": [],
            "rewritten_query": "hi",
        })


@pytest.fixture
def backend(monkeypatch):
    looked_up, saved = [], []

    def fake_customer(customer_id):
        looked_up.append(customer_id)
        return {"active": True, "external_id": f"EXT-{customer_id}"}

    async def fake_save(**kwargs):
        saved.append(kwargs)

    async def none(*a, **k):
        return None

    async def allowed(*a, **k):
        return 0

    monkeypatch.setattr(llm_limits, "llm_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(munomultiagent, "supervised_llm", FakeSupervisor())
    monkeypatch.setattr(chat_route, "getcustomerid", fake_customer)
    monkeypatch.setattr(chat_route, "save_conversation_to_sql", fake_save)
    monkeypatch.setattr(chat_route, "append_conversation_to_redis", none)
    monkeypatch.setattr(dependencies, "check_rate_limit", allowed)
    return looked_up, saved


def test_schema_has_no_customer_or_session_fields():
    assert "customer_id" not in chatrequest.model_fields
    assert "session_id" not in chatrequest.model_fields


def test_body_customer_and_session_are_ignored(backend):
    looked_up, saved = backend
    token = create_access_token({"customer_id": 90001, "first_name": "T", "last_name": "T"})

    response = TestClient(app).post(
        "/api/v1/chat",
        json={"message": "hello there", "customer_id": "20817", "session_id": "EXT-20817"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200                       # old apps still work
    assert response.json()["session_id"] == "EXT-90001"       # from the token's customer
    assert looked_up == [90001]
    assert saved[0]["authenticated_customer_id"] == 90001
    assert saved[0]["session_id"] == "EXT-90001"
