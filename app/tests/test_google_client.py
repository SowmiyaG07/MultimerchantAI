"""
Google Routes calls share ONE HTTP client per worker instead of
opening a new client (TCP + TLS connection) for every call.

A fake Google server (httpx.MockTransport) answers, so no real API
call is made and no real key is needed.
"""
from dotenv import load_dotenv

load_dotenv()

import asyncio  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.tools.near_bymerchant_tool as tool  # noqa: E402
from app.api.main import app  # noqa: E402

ROUTE = {"routes": [{"distanceMeters": 5000, "duration": "600s"}]}


@pytest.fixture
def fake_google(monkeypatch):
    """Count how many clients are created; answer every call."""
    created = []
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=ROUTE)

    def new_client():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                   timeout=tool.GOOGLE_TIMEOUT)
        created.append(client)
        return client

    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    monkeypatch.setattr(tool, "_new_google_client", new_client)
    monkeypatch.setattr(tool, "_google_client", None)
    monkeypatch.setattr(tool, "_google_client_loop", None)
    return created, requests


def route():
    return tool.get_route(33.2, -87.5, 33.3, -87.6)


def test_many_calls_share_one_client(fake_google):
    created, requests = fake_google

    async def main():
        results = [await route() for _ in range(5)]
        await tool.close_google_client()
        return results

    results = asyncio.run(main())
    assert len(requests) == 5
    assert len(created) == 1                       # before the fix: 5
    assert results[0] == {"distance_km": 5.0, "distance_miles": 3.1, "duration_minutes": 10}


def test_parallel_calls_share_one_client(fake_google):
    created, requests = fake_google

    async def main():
        await asyncio.gather(*[route() for _ in range(10)])
        await tool.close_google_client()

    asyncio.run(main())
    assert len(requests) == 10
    assert len(created) == 1


def test_closed_client_is_replaced(fake_google):
    created, _ = fake_google

    async def main():
        await route()
        await tool.close_google_client()
        await route()                              # new client after close
        await tool.close_google_client()

    asyncio.run(main())
    assert len(created) == 2
    assert all(client.is_closed for client in created)


def test_new_event_loop_gets_new_client(fake_google):
    created, _ = fake_google
    asyncio.run(route())
    asyncio.run(route())                           # different loop
    assert len(created) == 2


def test_key_is_sent_in_header_not_url(fake_google):
    _, requests = fake_google
    asyncio.run(route())
    assert requests[0].headers["X-Goog-Api-Key"] == "test-key"
    assert "test-key" not in str(requests[0].url)


def test_google_error_becomes_runtime_error(monkeypatch):
    def handler(request):
        return httpx.Response(500, json={"error": "boom"})

    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    monkeypatch.setattr(tool, "_new_google_client",
                        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(tool, "_google_client", None)

    with pytest.raises(RuntimeError, match="Google Routes API request failed"):
        asyncio.run(route())


def test_close_without_client_is_safe(monkeypatch):
    monkeypatch.setattr(tool, "_google_client", None)
    asyncio.run(tool.close_google_client())


def test_server_shutdown_closes_the_client(fake_google):
    created, _ = fake_google

    with TestClient(app):                          # runs lifespan startup/shutdown
        client = tool._new_google_client()
        tool._google_client = client

    assert client.is_closed
    assert tool._google_client is None
