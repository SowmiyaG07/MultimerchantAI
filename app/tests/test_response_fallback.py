"""
When the response LLM fails, the user gets readable text built from
the agent results, never raw Python data ([{'id': ...}]).
Fake LLM, so no Ollama, DB or Redis is needed.
"""
from dotenv import load_dotenv

load_dotenv()

import asyncio  # noqa: E402
from datetime import datetime  # noqa: E402

import pytest  # noqa: E402

import app.agent.llm_limits as llm_limits  # noqa: E402
import app.agent.response_agent as response_agent  # noqa: E402
from app.agent.response_agent import (  # noqa: E402
    FALLBACK_EMPTY_MESSAGE,
    ResponseAgent,
    _fallback_text,
)


class FailingLLM:
    async def ainvoke(self, prompt):
        raise ConnectionError("Ollama is unavailable")


MERCHANT = {
    "agent": "MerchantAgent",
    "results": [{
        "id": 101, "name": "Dreamland BBQ", "brand": None,
        "category": "Restaurants", "address": "5535 15th Ave",
        "city": "Tuscaloosa", "state": "AL", "phone": "205-555-0100",
        "website": "https://example.com", "social_media": {"facebook": "x"},
        "description": "A long marketing description that should be left out.",
    }],
}

OFFER = {
    "agent": "OfferAgent",
    "results": [
        {"id": 101, "name": "Dreamland BBQ", "city": "Tuscaloosa", "state": "AL",
         "offer": "Free dessert", "offer_details": "With any entree",
         "offer_valid_until": datetime(2026, 12, 31, 0, 0)},
        {"id": 102, "name": "Cafe Du Monde", "city": None, "state": None,
         "offer": None, "amount": 5, "min_purchase": 20,
         "offer_valid_until": None},
    ],
}

REDEMPTION = {
    "agent": "RedemptionAgent",
    "results": [
        {"summary": "You have 1 redemption from 2026-10-01 to 2026-10-31."},
        {"id": 101, "name": "Dreamland BBQ", "city": "Tuscaloosa", "state": "AL",
         "redeemed_on": "2026-10-05 07:30 PM", "amount_spent": "$25.00",
         "offer": "Free dessert", "offer_type": None},
    ],
}


def assert_no_raw_python(text):
    for marker in ("[{", "{'", "'id':", "None", "datetime("):
        assert marker not in text, f"raw data {marker!r} in: {text}"


def test_merchant_offer_redemption_fallback_is_readable():
    text = _fallback_text([MERCHANT, OFFER, REDEMPTION])
    print("\n" + text)

    assert_no_raw_python(text)
    assert text.startswith("Here is what I found:")
    assert "- Dreamland BBQ" in text
    assert "Address: 5535 15th Ave, Tuscaloosa, AL" in text
    assert "Phone: 205-555-0100" in text
    assert "Valid until: 2026-12-31" in text                 # date, no time
    assert "$5 off with a minimum purchase of $20" in text   # amount offer
    assert "You have 1 redemption from 2026-10-01 to 2026-10-31." in text
    assert "Amount spent: $25.00" in text
    assert "marketing description" not in text              # long text left out
    assert "facebook" not in text


def test_messages_are_kept_as_they_are():
    text = _fallback_text([
        {"agent": "MerchantAgent", "results": "No matching merchants were found."},
        OFFER,
    ])
    assert "No matching merchants were found." in text
    assert "- Cafe Du Monde" in text
    assert_no_raw_python(text)


def test_record_list_is_capped():
    many = {"agent": "MerchantAgent",
            "results": [{"name": f"Store {i}"} for i in range(25)]}
    text = _fallback_text([many])
    assert text.count("- Store") == response_agent.MAX_FALLBACK_RECORDS


def test_nothing_usable_gives_friendly_message():
    assert _fallback_text([{"agent": "OfferAgent", "results": []}]) == FALLBACK_EMPTY_MESSAGE


@pytest.fixture
def failing_llm(monkeypatch):
    monkeypatch.setattr(llm_limits, "llm_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(response_agent, "llm", FailingLLM())


def test_response_agent_uses_readable_fallback_when_llm_fails(failing_llm):
    state = {
        "message": "Dreamland BBQ offers and did I redeem there?",
        "all_agent_results": [OFFER, REDEMPTION],
    }
    result = asyncio.run(ResponseAgent(state))
    answer = result["response"]

    assert_no_raw_python(answer)
    assert "Free dessert" in answer
    assert "You have 1 redemption" in answer
