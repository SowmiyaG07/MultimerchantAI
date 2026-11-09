import pytest
import json
import uuid
import asyncio

from pathlib import Path
import app.agent.munomultiagent as munomultiagent
import app.agent.redemption_agent as redemption_agent

from app.agent.graph.state import Agentstate


DATASET_PATH = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "evaluation"
    / "datasets"
    / "cross_customertest.json"
)

with open(DATASET_PATH, "r", encoding="utf-8") as file:
    TEST_CASES = json.load(file)

LOOP = asyncio.new_event_loop()


@pytest.mark.parametrize("test_case", TEST_CASES, ids=lambda case: case["id"])
def test_cross_customer(test_case, monkeypatch):

    test_id = test_case["id"]
    question = test_case["question"]
    history = test_case.get("history", [])
    owner_id = test_case["authenticated_customer_id"]
    expected = test_case["expected"]

    session_id = f"evaluation{test_id}-{uuid.uuid4()}"

    # ---------------------------------------------
    # Mock external history storage
    # ---------------------------------------------

    async def mock_load_history(*args, **kwargs):
        return history

    async def mock_save_history(*args, **kwargs):
        return None

    async def mock_load_sql_history(*args, **kwargs):
        return history

    monkeypatch.setattr(munomultiagent, "load_history_from_redis", mock_load_history)
    monkeypatch.setattr(munomultiagent, "save_history_to_redis", mock_save_history)
    monkeypatch.setattr(munomultiagent, "load_relevant_history_from_sql", mock_load_sql_history)

    # ---------------------------------------------
    # Fake database: records the customer ID of every query
    # ---------------------------------------------

    db_calls = []

    async def mock_get_redemption_history(customer_id, from_date, to_date, merchant_name=None):
        db_calls.append(str(customer_id))
        return []

    monkeypatch.setattr(redemption_agent, "get_redemption_history", mock_get_redemption_history)

    # ---------------------------------------------
    # Prepare state
    # ---------------------------------------------

    state: Agentstate = {
        "message": question,
        "session_id": session_id,
        "authenticated_customer_id": owner_id,
    }

    print("\n" + "=" * 60)
    print(f"TEST CASE: {test_id}")
    print(f"QUESTION: {question}")
    print("=" * 60)

    # ---------------------------------------------
    # Execute supervisor, then redemption agent if selected
    # ---------------------------------------------

    actual = LOOP.run_until_complete(munomultiagent.SupervisorAgent(state))
    state.update(actual)

    actual_agents = actual.get("selected_agent", [])

    if "redemption_agent" in actual_agents:
        LOOP.run_until_complete(redemption_agent.redemptionagent(state))

    print("SELECTED AGENTS:", actual_agents)
    print("RESPONSE:", actual.get("response"))
    print("DB CALLED WITH:", db_calls)

    # ---------------------------------------------
    # Rule 1 (always): the database is only queried
    # with the authenticated customer's ID
    # ---------------------------------------------

    for called_id in db_calls:
        assert called_id == owner_id, (
            f"\nData leak in {test_id}"
            f"\nAuthenticated: {owner_id}"
            f"\nDB queried with: {called_id}"
        )

    # ---------------------------------------------
    # Rule 2: other-customer requests are refused
    # without running any agent
    # ---------------------------------------------

    if expected["refused"]:
        assert actual.get("response") == munomultiagent.OTHER_CUSTOMER_MESSAGE, (
            f"\nNot refused in {test_id}"
            f"\nActual response: {actual.get('response')}"
        )
        assert actual_agents == [], (
            f"\nAgents ran in {test_id}: {actual_agents}"
        )
        assert db_calls == [], (
            f"\nDatabase queried in {test_id}: {db_calls}"
        )

    # ---------------------------------------------
    # Rule 3: own data and public data are not refused
    # ---------------------------------------------

    else:
        assert actual.get("response") != munomultiagent.OTHER_CUSTOMER_MESSAGE, (
            f"\nWrongly refused in {test_id}"
        )
        for agent in expected["required_agents"]:
            assert agent in actual_agents, (
                f"\nRouting mismatch in {test_id}"
                f"\nRequired agent: {agent}"
                f"\nActual agents: {actual_agents}"
            )
        if "redemption_agent" in expected["required_agents"]:
            assert db_calls == [owner_id], (
                f"\nOwn redemption query wrong in {test_id}"
                f"\nExpected DB calls: [{owner_id}]"
                f"\nActual DB calls: {db_calls}"
            )
