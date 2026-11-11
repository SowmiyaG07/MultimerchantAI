"""
Health endpoints:
  GET /api/v1/health        liveness  - always 200 while the process runs
  GET /api/v1/health/ready  readiness - 200 Ready / 200 Degraded / 503 NotReady

The dependency checks are replaced by fakes, so the tests do not need
SQL Server, Redis or Ollama.
"""
from dotenv import load_dotenv

load_dotenv()

import asyncio  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.api.route.health as health  # noqa: E402
from app.api.main import app  # noqa: E402

client = TestClient(app)


async def ok():
    return None


async def down():
    raise ConnectionError("db.internal.example:1433 refused")


async def hangs():
    await asyncio.sleep(30)


@pytest.fixture
def checks(monkeypatch):
    monkeypatch.setattr(health, "CHECK_TIMEOUT_SECONDS", 0.5)

    def set_checks(sql=ok, llm=ok, redis=ok):
        monkeypatch.setattr(health, "REQUIRED_CHECKS", {"sql": sql, "llm": llm})
        monkeypatch.setattr(health, "OPTIONAL_CHECKS", {"redis": redis})

    return set_checks


def statuses(body):
    return {name: check["status"] for name, check in body["checks"].items()}


# ---------------------------------------------
# Liveness and URLs
# ---------------------------------------------

def test_liveness_url():
    response = client.get("/api/v1/health", follow_redirects=False)
    assert response.status_code == 200
    assert response.json() == {"status": "Healthy"}


def test_old_double_prefix_url_is_gone():
    assert client.get("/api/v1/health/health/").status_code == 404


# ---------------------------------------------
# Readiness
# ---------------------------------------------

def test_all_up_is_ready(checks):
    checks()
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "Ready"
    assert statuses(response.json()) == {"sql": "ok", "llm": "ok", "redis": "ok"}


def test_redis_down_is_degraded_but_still_200(checks):
    checks(redis=down)
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200          # chat still works via fallbacks
    assert response.json()["status"] == "Degraded"
    assert statuses(response.json())["redis"] == "down"


@pytest.mark.parametrize("broken", ["sql", "llm"])
def test_sql_or_llm_down_is_503(checks, broken):
    checks(**{broken: down})
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "NotReady"
    assert statuses(response.json())[broken] == "down"


def test_hanging_dependency_times_out_quickly(checks):
    checks(llm=hangs, redis=hangs)
    start = time.perf_counter()
    response = client.get("/api/v1/health/ready")
    took = time.perf_counter() - start

    assert response.status_code == 503
    assert took < 2.0                           # 0.5s timeout, checks run together


def test_error_details_are_not_returned(checks):
    checks(sql=down)
    text = client.get("/api/v1/health/ready").text
    assert "db.internal.example" not in text
    assert "refused" not in text
