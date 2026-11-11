import pytest
import json
import uuid
import asyncio
import inspect

from pathlib import Path
import app.agent.munomultiagent as munomultiagent

from app.agent.graph.state import Agentstate


DATASET_PATH = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "evaluation"
    / "datasets"
    # Routing cases: expected.selected_agent / merchant_name /
    # needs_clarification. (cross_customertest.json belongs to
    # test_cross_customer.py and has a different shape.)
    / "supervisor_routing_tests.json"
)

with open(DATASET_PATH, "r", encoding="utf-8") as file:
    TEST_CASES = json.load(file)

LOOP = asyncio.new_event_loop()

@pytest.mark.parametrize("test_case", TEST_CASES, ids=lambda case: case["id"])
def test_supervisor_evaluation(test_case, monkeypatch):

    test_id = test_case["id"]
    question = test_case["question"]
    history = test_case.get("history", [])
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

    monkeypatch.setattr(
        munomultiagent,
        "load_history_from_redis",
        mock_load_history
    )

    monkeypatch.setattr(
        munomultiagent,
        "save_history_to_redis",
        mock_save_history
    )

    monkeypatch.setattr(
        munomultiagent,
        "load_relevant_history_from_sql",
        mock_load_sql_history
    )

    # ---------------------------------------------
    # Prepare supervisor state
    # ---------------------------------------------

    state: Agentstate = {
        "message": question,
        "session_id": session_id,
    }

    for field in ("customer_id", "latitude", "longitude", "from_date", "to_date"):
        if field in test_case:
            state[field] = test_case[field]

    print("\n" + "=" * 60)
    print(f"TEST CASE: {test_id}")
    print(f"QUESTION: {question}")
    print("=" * 60)

    # ---------------------------------------------
    # Execute supervisor
    # ---------------------------------------------
    
    
    
    actual =  LOOP.run_until_complete(munomultiagent.SupervisorAgent(state))
    
    print("ACTUAL TYPE:", type(actual))
    print("ACTUAL OUTPUT:", actual)
    
    for key, value in actual.items():
        print(
        key,
        type(value),
        "Is coroutine:", inspect.iscoroutine(value)
    )

    print("\nEXPECTED OUTPUT:")
    print(json.dumps(expected, indent=4, ensure_ascii=False))

    print("\nACTUAL OUTPUT:")
    print(json.dumps(actual, indent=4, ensure_ascii=False))

    # ---------------------------------------------
    # Evaluate routing
    # ---------------------------------------------

    expected_agents = expected["selected_agent"]
    actual_agents = actual.get("selected_agent", [])

    assert (
        set(actual_agents) == set(expected_agents)
        and len(actual_agents) == len(expected_agents)
    ), (
        f"\nRouting mismatch in {test_id}"
        f"\nExpected agents: {expected_agents}"
        f"\nActual agents: {actual_agents}"
    )

    # ---------------------------------------------
    # Evaluate clarification
    # ---------------------------------------------

    expected_clarification = expected["needs_clarification"]
    actual_clarification = actual.get("needs_clarification")

    assert actual_clarification == expected_clarification, (
        f"\nClarification mismatch in {test_id}"
        f"\nExpected: {expected_clarification}"
        f"\nActual: {actual_clarification}"
    )