"""
LLM queue timeout: a call that cannot get a semaphore slot within
LLM_QUEUE_TIMEOUT_SECONDS raises LLMBusyError, and /chat returns 503.

Fake slow LLM, so the test is fast and needs no Ollama, DB or Redis.
"""
from dotenv import load_dotenv

load_dotenv()

import asyncio  # noqa: E402
import time  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402

import app.agent.llm_limits as llm_limits  # noqa: E402
import app.agent.munomultiagent as munomultiagent  # noqa: E402
import app.api.dependencies as dependencies  # noqa: E402
import app.api.route.chat as chat_route  # noqa: E402
from app.agent.llm_limits import LLMBusyError, call_llm  # noqa: E402
from app.api.jwt import create_access_token  # noqa: E402
from app.api.main import app  # noqa: E402


class SlowLLM:
    def __init__(self, seconds, result="ok"):
        self.seconds = seconds
        self.result = result
        self.calls = 0

    async def ainvoke(self, prompt):
        self.calls += 1
        await asyncio.sleep(self.seconds)
        return self.result


@pytest.fixture
def limits(monkeypatch):
    """1 slot, 0.5s queue wait, 5s call timeout."""
    def apply(slots=1, queue=0.5, call=5.0):
        monkeypatch.setattr(llm_limits, "llm_semaphore", asyncio.Semaphore(slots))
        monkeypatch.setattr(llm_limits, "LLM_QUEUE_TIMEOUT_SECONDS", queue)
        monkeypatch.setattr(llm_limits, "LLM_TIMEOUT_SECONDS", call)
    return apply


# ---------------------------------------------
# call_llm on its own
# ---------------------------------------------

def test_waiter_gives_up_after_queue_timeout(limits):
    async def main():
        limits(slots=1, queue=0.5)
        llm = SlowLLM(2.0)
        start = time.perf_counter()
        results = await asyncio.gather(
            call_llm(llm, "p", "a"),
            call_llm(llm, "p", "b"),
            return_exceptions=True,
        )
        return results, time.perf_counter() - start, llm.calls

    results, total, calls = asyncio.run(main())
    assert results[0] == "ok"
    assert isinstance(results[1], LLMBusyError)
    assert calls == 1                 # the busy call never reached the LLM
    assert total < 2.5


def test_busy_error_comes_fast(limits):
    async def main():
        limits(slots=1, queue=0.3)
        llm = SlowLLM(3.0)
        first = asyncio.create_task(call_llm(llm, "p", "a"))
        await asyncio.sleep(0.05)
        start = time.perf_counter()
        with pytest.raises(LLMBusyError):
            await call_llm(llm, "p", "b")
        waited = time.perf_counter() - start
        first.cancel()
        return waited

    waited = asyncio.run(main())
    assert 0.25 <= waited < 0.8       # waited the queue timeout, not 3s


def test_slot_released_after_busy_and_after_timeout(limits):
    async def main():
        limits(slots=1, queue=0.2, call=0.3)
        with pytest.raises(TimeoutError):          # call timeout
            await call_llm(SlowLLM(1.0), "p", "a")
        assert llm_limits.llm_semaphore._value == 1

        holder = asyncio.create_task(call_llm(SlowLLM(0.25), "p", "a"))
        await asyncio.sleep(0.01)
        with pytest.raises(LLMBusyError):          # queue timeout
            await call_llm(SlowLLM(0.0), "p", "b")
        await holder
        assert llm_limits.llm_semaphore._value == 1   # nothing leaked

        assert await call_llm(SlowLLM(0.0), "p", "c") == "ok"

    asyncio.run(main())


def test_waiter_inside_queue_timeout_still_runs(limits):
    async def main():
        limits(slots=1, queue=2.0)
        llm = SlowLLM(0.3)
        return await asyncio.gather(*[call_llm(llm, "p", str(i)) for i in range(4)])

    # 4 x 0.3s one after another = 1.2s, all inside the 2s queue wait
    assert asyncio.run(main()) == ["ok"] * 4


# ---------------------------------------------
# Through the API: 503 + Retry-After
# ---------------------------------------------

@pytest.fixture
def api_fakes(monkeypatch):
    def fake_customer(customer_id):
        return {"active": True, "external_id": f"EXT-{customer_id}"}

    async def none(*a, **k):
        return None

    async def allowed(*a, **k):
        return 0

    monkeypatch.setattr(chat_route, "getcustomerid", fake_customer)
    monkeypatch.setattr(chat_route, "save_conversation_to_sql", none)
    monkeypatch.setattr(chat_route, "append_conversation_to_redis", none)
    monkeypatch.setattr(dependencies, "check_rate_limit", allowed)


def supervisor_decision():
    return munomultiagent.SupervisorDecision.model_validate({
        "business_related": False,      # graph ends after the supervisor
        "history_needed": False,
        "other_customer_requested": False,
        "selected_agent": [],
        "rewritten_query": "hi",
    })


async def send(users):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30) as client:
        async def one(cid):
            token = create_access_token({"customer_id": cid, "first_name": "T", "last_name": str(cid)})
            start = time.perf_counter()
            r = await client.post("/api/v1/chat", json={"message": "hello there"},
                                  headers={"Authorization": f"Bearer {token}"})
            return r, time.perf_counter() - start
        return await asyncio.gather(*[one(90001 + i) for i in range(users)])


def test_api_returns_503_when_llm_busy(limits, api_fakes, monkeypatch):
    llm = SlowLLM(1.5, supervisor_decision())
    monkeypatch.setattr(munomultiagent, "supervised_llm", llm)

    async def main():
        limits(slots=1, queue=0.5)
        return await send(3)

    results = asyncio.run(main())
    statuses = sorted(r.status_code for r, _ in results)
    print("\nstatuses:", statuses, "times:", [round(t, 2) for _, t in results])

    assert statuses == [200, 503, 503]
    assert llm.calls == 1                         # the 2 busy ones never reached the LLM

    for r, took in results:
        if r.status_code == 503:
            detail = r.json()["detail"]
            assert detail["code"] == "LLM_BUSY"
            assert detail["trace_id"]
            assert r.headers["Retry-After"] == str(detail["retry_after_seconds"])
            assert took < 1.2                     # returned after ~0.5s, not after 1.5s+


def test_api_all_200_when_queue_is_short_enough(limits, api_fakes, monkeypatch):
    llm = SlowLLM(0.2, supervisor_decision())
    monkeypatch.setattr(munomultiagent, "supervised_llm", llm)

    async def main():
        limits(slots=1, queue=5.0)
        return await send(3)

    results = asyncio.run(main())
    assert [r.status_code for r, _ in results] == [200, 200, 200]
    assert llm.calls == 3
