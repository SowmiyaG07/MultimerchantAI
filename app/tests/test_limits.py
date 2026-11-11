"""
LLM concurrency limit and timeout, chat rate limit (Redis and the
memory fallback), and the chat history cache while Redis is down.

Fake LLM and fake Redis: no Ollama or Redis needed.
"""
from dotenv import load_dotenv

load_dotenv()

import asyncio  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from redis.exceptions import RedisError  # noqa: E402

import app.agent.llm_limits as llm_limits  # noqa: E402
import app.agent.munomultiagent as munomultiagent  # noqa: E402
import app.agent.response_agent as response_agent  # noqa: E402
import app.api.dependencies as dependencies  # noqa: E402
import app.cache.redis_cache as redis_cache  # noqa: E402

# The concurrency tests use their own limit, independent of .env.
TEST_SLOTS = 4


# ---------------------------------------------
# Fakes
# ---------------------------------------------

class FakeLLM:
    """Records how many calls run at the same time."""

    def __init__(self, delay=0.1, error=None):
        self.delay = delay
        self.error = error
        self.running = 0
        self.max_running = 0

    async def ainvoke(self, prompt):
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            await asyncio.sleep(self.delay)
            if self.error:
                raise self.error
            return "ok"
        finally:
            self.running -= 1


class FakePipeline:
    """Supports the three commands check_rate_limit uses."""

    def __init__(self, store):
        self.store = store
        self.commands = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def set(self, key, value, ex=None, nx=False):
        self.commands.append(("set", key, value, ex, nx))
        return self

    def incr(self, key):
        self.commands.append(("incr", key))
        return self

    def ttl(self, key):
        self.commands.append(("ttl", key))
        return self

    async def execute(self):
        results = []
        for command in self.commands:
            if command[0] == "set":
                _, key, value, ex, nx = command
                if nx and key in self.store:
                    results.append(None)
                else:
                    self.store[key] = [value, ex]
                    results.append(True)
            elif command[0] == "incr":
                self.store[command[1]][0] += 1
                results.append(self.store[command[1]][0])
            else:
                results.append(self.store[command[1]][1])
        return results


class FakeRedis:
    def __init__(self):
        self.store = {}

    def pipeline(self, transaction=True):
        return FakePipeline(self.store)


class BrokenRedis:
    def pipeline(self, transaction=True):
        raise RedisError("down")


@pytest.fixture(autouse=True)
def fresh_semaphore(monkeypatch):
    # Each asyncio.run() uses a new event loop, so give each test
    # its own semaphore with TEST_SLOTS slots.
    monkeypatch.setattr(llm_limits, "LLM_MAX_CONCURRENT_CALLS", TEST_SLOTS)
    monkeypatch.setattr(llm_limits, "llm_semaphore", asyncio.Semaphore(TEST_SLOTS))


# ---------------------------------------------
# Settings
# ---------------------------------------------

def test_settings_come_from_env():
    """Values match .env (or the code defaults), whatever they are set to."""
    assert llm_limits.LLM_TIMEOUT_SECONDS == float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
    assert llm_limits.LLM_QUEUE_TIMEOUT_SECONDS == float(os.getenv("LLM_QUEUE_TIMEOUT_SECONDS", "20"))
    assert dependencies.CHAT_RATE_LIMIT == int(os.getenv("CHAT_RATE_LIMIT", "100"))
    assert dependencies.CHAT_RATE_WINDOW == int(os.getenv("CHAT_RATE_WINDOW", "60"))
    assert llm_limits.LLM_TIMEOUT_SECONDS > 0
    assert dependencies.CHAT_RATE_LIMIT > 0


# ---------------------------------------------
# LLM concurrency
# ---------------------------------------------

def test_at_most_slot_count_llm_calls_run_together():
    fake = FakeLLM(delay=0.1)

    async def run():
        await asyncio.gather(*[
            llm_limits.call_llm(fake, "prompt", name="test")
            for _ in range(10)
        ])

    asyncio.run(run())

    # TEST_SLOTS, not 1: calls still run in parallel up to the limit.
    assert fake.max_running == TEST_SLOTS


# ---------------------------------------------
# LLM timeout
# ---------------------------------------------

def test_timeout_raises_and_releases_slot(monkeypatch):
    monkeypatch.setattr(llm_limits, "LLM_TIMEOUT_SECONDS", 0.05)
    slow = FakeLLM(delay=5)

    async def run():
        with pytest.raises(TimeoutError):
            await llm_limits.call_llm(slow, "prompt", name="test")
        return llm_limits.llm_semaphore._value

    start = time.perf_counter()
    free_slots = asyncio.run(run())

    assert time.perf_counter() - start < 1
    assert free_slots == TEST_SLOTS


def test_error_releases_slot():
    failing = FakeLLM(delay=0, error=ValueError("boom"))

    async def run():
        with pytest.raises(ValueError):
            await llm_limits.call_llm(failing, "prompt", name="test")
        return llm_limits.llm_semaphore._value

    assert asyncio.run(run()) == TEST_SLOTS


def test_supervisor_timeout_returns_error_message(monkeypatch):
    monkeypatch.setattr(llm_limits, "LLM_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(munomultiagent, "supervised_llm", FakeLLM(delay=5))

    output = asyncio.run(munomultiagent.SupervisorAgent({
        "message": "Show my redemption history",
        "session_id": "TEST",
        "authenticated_customer_id": "10572",
    }))

    assert output["selected_agent"] == []
    assert output["response"] == munomultiagent.SUPERVISOR_ERROR_MESSAGE


def test_response_agent_timeout_returns_fallback(monkeypatch):
    monkeypatch.setattr(llm_limits, "LLM_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(response_agent, "llm", FakeLLM(delay=5))

    output = asyncio.run(response_agent.ResponseAgent({
        "message": "What offers does Dreamland BBQ have?",
        "all_agent_results": [],
    }))

    assert output["response"]


# ---------------------------------------------
# Chat rate limit
# ---------------------------------------------

def test_100_allowed_then_101st_blocked(monkeypatch):
    monkeypatch.setattr(redis_cache, "redis_client", FakeRedis())

    async def run():
        results = []
        for _ in range(101):
            results.append(await redis_cache.check_rate_limit(
                "chat_rate:10572", limit=100, window_seconds=60))
        return results

    results = asyncio.run(run())

    assert results[:100] == [0] * 100
    assert results[100] == 60


def test_customers_have_independent_limits(monkeypatch):
    monkeypatch.setattr(redis_cache, "redis_client", FakeRedis())

    async def run():
        for _ in range(101):
            await redis_cache.check_rate_limit("chat_rate:10572", 100, 60)
        return await redis_cache.check_rate_limit("chat_rate:10499", 100, 60)

    assert asyncio.run(run()) == 0


@pytest.fixture(autouse=True)
def reset_fallback(monkeypatch):
    monkeypatch.setattr(redis_cache, "_local_counters", {})
    monkeypatch.setattr(redis_cache, "_redis_down_until", 0.0)
    monkeypatch.setattr(redis_cache, "_stale_sessions", set())


def test_redis_down_uses_memory_limit(monkeypatch):
    monkeypatch.setattr(redis_cache, "redis_client", BrokenRedis())

    async def run():
        return [await redis_cache.check_rate_limit("chat_rate:10572", 100, 60)
                for _ in range(101)]

    results = asyncio.run(run())

    assert results[:100] == [0] * 100
    assert 1 <= results[100] <= 60


def test_redis_down_customers_still_independent(monkeypatch):
    monkeypatch.setattr(redis_cache, "redis_client", BrokenRedis())

    async def run():
        for _ in range(101):
            await redis_cache.check_rate_limit("chat_rate:10572", 100, 60)
        return await redis_cache.check_rate_limit("chat_rate:10499", 100, 60)

    assert asyncio.run(run()) == 0


def test_redis_skipped_after_failure(monkeypatch):
    calls = []

    class CountingBrokenRedis:
        def pipeline(self, transaction=True):
            calls.append(1)
            raise RedisError("down")

    monkeypatch.setattr(redis_cache, "redis_client", CountingBrokenRedis())

    async def run():
        for _ in range(5):
            await redis_cache.check_rate_limit("chat_rate:10572", 100, 60)

    asyncio.run(run())

    # Only the first request tries Redis; the next 4 go straight to memory.
    assert len(calls) == 1


def test_redis_retried_after_retry_period(monkeypatch):
    monkeypatch.setattr(redis_cache, "redis_client", FakeRedis())
    monkeypatch.setattr(redis_cache, "_redis_down_until", 0.0)

    result = asyncio.run(redis_cache.check_rate_limit("chat_rate:10572", 100, 60))

    assert result == 0
    assert "chat_rate:10572" in redis_cache.redis_client.store


def test_key_uses_customer_id_from_jwt(monkeypatch):
    keys = []

    async def fake_check(key, limit, window_seconds):
        keys.append((key, limit, window_seconds))
        return 0

    monkeypatch.setattr(dependencies, "check_rate_limit", fake_check)

    customer_id = asyncio.run(
        dependencies.check_chat_rate_limit(customer_id=10572))

    assert customer_id == 10572
    assert keys == [("chat_rate:10572", dependencies.CHAT_RATE_LIMIT, dependencies.CHAT_RATE_WINDOW)]


def test_chat_api_returns_429_with_retry_after(monkeypatch):
    from app.api.main import app

    async def fake_check(key, limit, window_seconds):
        return 42

    monkeypatch.setattr(dependencies, "check_rate_limit", fake_check)
    app.dependency_overrides[dependencies.get_current_customer_id] = (
        lambda: 10572
    )

    try:
        response = TestClient(app).post(
            "/api/v1/chat", json={"message": "Show my redemption history"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "42"
    assert response.json()["detail"]["code"] == "RATE_LIMITED"


# ---------------------------------------------
# Chat history cache while Redis is down
# ---------------------------------------------

class FakeHistoryRedis:
    def __init__(self, broken=False):
        self.data = {}
        self.broken = broken
        self.calls = 0

    def _check(self):
        self.calls += 1
        if self.broken:
            raise RedisError("down")

    async def get(self, key):
        self._check()
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self._check()
        self.data[key] = value

    async def delete(self, key):
        self._check()
        self.data.pop(key, None)


def test_history_load_returns_empty_when_redis_down(monkeypatch):
    monkeypatch.setattr(redis_cache, "redis_client", FakeHistoryRedis(broken=True))

    assert asyncio.run(redis_cache.load_history_from_redis("S1")) == []


def test_history_calls_skip_redis_after_failure(monkeypatch):
    fake = FakeHistoryRedis(broken=True)
    monkeypatch.setattr(redis_cache, "redis_client", fake)

    async def run():
        await redis_cache.load_history_from_redis("S1")          # fails, marks down
        await redis_cache.save_history_to_redis("S1", [{"role": "user", "content": "q"}])
        await redis_cache.append_conversation_to_redis("S1", "q", "a")
        await redis_cache.check_rate_limit("chat_rate:1", 100, 60)

    asyncio.run(run())

    # Only the first call touched Redis; the rest skipped it.
    assert fake.calls == 1


def test_missed_turn_makes_cache_stale_until_cleared(monkeypatch):
    fake = FakeHistoryRedis()
    old = [{"role": "user", "content": "Tell me about Dreamland BBQ"},
           {"role": "assistant", "content": "Dreamland BBQ is ..."}]
    fake.data["chat_history:S1"] = __import__("json").dumps(old)
    monkeypatch.setattr(redis_cache, "redis_client", fake)

    # Redis unreachable while a new turn is saved.
    fake.broken = True
    asyncio.run(redis_cache.append_conversation_to_redis("S1", "Tell me about Cafe Du Monde", "..."))

    # Redis back (retry period over): the old cache must not be used.
    fake.broken = False
    monkeypatch.setattr(redis_cache, "_redis_down_until", 0.0)

    first = asyncio.run(redis_cache.load_history_from_redis("S1"))

    assert first == []                         # caller uses SQL instead
    assert "chat_history:S1" not in fake.data  # stale cache deleted
    assert "S1" not in redis_cache._stale_sessions
