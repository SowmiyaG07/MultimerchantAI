

import json

from app.agent.graph.state import Agentstate

from app.agent.munomultiagent import llm
from app.utilities.log import Applogger
from app.agent.llm_limits import call_llm, LLMBusyError

logger = Applogger.get_loggerdata(__name__)


SECTION_TITLES = {
    "MerchantAgent": "MERCHANT DETAILS",
    "OfferAgent": "OFFERS AND DEALS",
    "NearbyAgent": "DISTANCE AND NEARBY PLACES",
    "RedemptionAgent": "THE USER'S REDEMPTIONS",
}

# ---------------------------------------------
# Fallback answer when the response LLM fails
# ---------------------------------------------

FALLBACK_TITLES = {
    "MerchantAgent": "Merchants:",
    "OfferAgent": "Offers:",
    "NearbyAgent": "Nearby places:",
    "RedemptionAgent": "Your redemptions:",
}

# (field, label) in the order they are shown. Everything else (ids,
# long descriptions, social media) is left out.
FALLBACK_FIELDS = [
    ("redeemed_on", "Redeemed on"),
    ("amount_spent", "Amount spent"),
    ("offer", "Offer"),
    ("offer_details", "Details"),
    ("offer_valid_until", "Valid until"),
    ("category", "Category"),
    ("location", "Address"),
    ("phone", "Phone"),
    ("distance", "Distance"),
    ("website", "Website"),
]

MAX_FALLBACK_RECORDS = 10

FALLBACK_EMPTY_MESSAGE = (
    "Sorry, I couldn't prepare an answer right now. Please try again."
)


def _plain(value) -> str:
    """One value as short text: dates without time, no None."""
    if value is None:
        return ""
    if hasattr(value, "date") and hasattr(value, "hour"):   # datetime
        return value.date().isoformat()
    if hasattr(value, "isoformat"):                        # date
        return value.isoformat()
    return str(value).strip()


def _format_record(record: dict) -> str:
    """One merchant / offer / redemption record as readable lines."""
    record = dict(record)

    # Address, city and state on one line
    location = ", ".join(
        _plain(record.get(key))
        for key in ("address", "city", "state")
        if _plain(record.get(key))
    )
    record["location"] = location

    # Amount-based offers have no offer text
    if not _plain(record.get("offer")) and _plain(record.get("amount")):
        offer = f"${_plain(record['amount'])} off"
        if _plain(record.get("min_purchase")):
            offer += f" with a minimum purchase of ${_plain(record['min_purchase'])}"
        record["offer"] = offer

    lines = [f"- {_plain(record.get('name')) or 'Merchant'}"]
    for key, label in FALLBACK_FIELDS:
        value = _plain(record.get(key))
        if value:
            lines.append(f"  {label}: {value}")
    return "\n".join(lines)


def _fallback_text(all_agent_results: list[dict]) -> str:
    """
    Build a plain-text answer from the agent results without the LLM.
    Messages are kept as they are; record lists become short lists.
    """
    sections = []

    for item in all_agent_results:
        results = item.get("results")

        # A plain message such as "No matching merchants were found."
        if isinstance(results, str):
            if results.strip():
                sections.append(results.strip())
            continue

        if not isinstance(results, list):
            continue

        lines = []
        shown = 0
        for record in results:
            if not isinstance(record, dict):
                continue
            # Redemption summary line: "You have 3 redemptions from ..."
            if set(record) == {"summary"}:
                lines.append(_plain(record["summary"]))
                continue
            if shown >= MAX_FALLBACK_RECORDS:
                continue
            lines.append(_format_record(record))
            shown += 1

        if lines:
            title = FALLBACK_TITLES.get(item.get("agent"), "Results:")
            sections.append(title + "\n" + "\n".join(lines))

    if not sections:
        return FALLBACK_EMPTY_MESSAGE

    return "Here is what I found:\n\n" + "\n\n".join(sections)


async def ResponseAgent(state: Agentstate) -> dict:
    # Step 1: Get results from each agent
    
     # 1. Current user message
    message = state.get("message", "")
    
    rewritten = state.get("expanded_query") or message

    # 2. Previous conversation
   # chat_history = state.get("chat_history", [])

    # 3. Results combined by reducer
    all_agent_results = state.get("all_agent_results", [])
    
        # Nothing came back
    if not all_agent_results:
        return {"response": "I couldn't find any relevant results."}

    # One plain message (location, route, error): already the answer
    if len(all_agent_results) == 1 and isinstance(all_agent_results[0]["results"], str):
        return {"response": all_agent_results[0]["results"]}

    # Shown if the LLM fails: plain text, never raw Python data
    fallback = _fallback_text(all_agent_results)

    
    # 4. Convert history and results into text
    #history_text = json.dumps(
        #chat_history,
       # indent=2,
        #default=str
    #)
    sections = []
    for item in all_agent_results:
        title = SECTION_TITLES.get(item["agent"], item["agent"])
        body = json.dumps(item["results"], indent=2, default=str)
        sections.append(f"### {title}\n{body}")
    result_text = "\n\n".join(sections)
    prompt = f"""
You are MunomerchantAI, a merchant assistant.

CURRENT USER MESSAGE:
{message}



DATABASE RESULTS FROM AGENTS:
{result_text}




INSTRUCTIONS:

GENERAL RULES:
1. Answer the current user message using ONLY the supplied database results.
2. Consider the results from EVERY selected agent.
3. Never ignore a successful result from an agent.
4. Do not invent merchants, offers, addresses, distances, dates or amounts.
5. Copy merchant names, offer names, numbers, dates and amounts exactly as given.
6. If a result section contains a message (for example, "Please enable location services"), communicate that message appropriately.
7. If the requested information is not available in the results, clearly say so.
8. Keep the answer short, clear and friendly.
9. Never mention agents, database queries or internal processing.

MULTI-AGENT RESULT HANDLING:
10. Treat each agent's result as an independent source of information.
11. When multiple agents are selected, combine their findings into ONE response.
12. Do not let an empty result from one agent override a successful result from another agent.
13. An empty redemption result means no matching redemption records were found. It does NOT mean that no offer exists.
14. An empty offer result means no matching offer was found. It does NOT mean that no redemption exists.

REDEMPTION QUERIES:
15. Use redemption results to determine whether the customer has redeemed an offer.
16. If redemption results are empty, clearly state that no matching redemption was found for the requested date range.
17. Do not claim that the customer has never redeemed an offer unless the supplied results establish that.

OFFER QUERIES:
18. Use offer results to determine whether an offer exists.
19. Report the offer details, discount and validity date when available.
20. If the offer validity date is available, communicate it accurately.
21. Do not claim an offer is currently available if the supplied information does not establish its current validity.

COMBINED REDEMPTION + OFFER QUERIES:
22. When the user asks whether an offer has already been used AND whether it is still available:
    a. Check redemption results independently.
    b. Check offer results independently.
    c. Combine both findings into one answer.
    d. Clearly communicate both redemption status and offer availability.
23. If redemption results are empty but offer results contain a valid offer, report both findings.
24. If offer results are empty but redemption results contain records, report the redemption information and explain that no matching current offer was found.
25. If both results are empty, communicate that neither matching redemption records nor matching offers were found.

MERCHANT AND OFFER MAPPING:
26. Match offers to merchants using merchant names and IDs when available.
27. When merchants and offers share the same ID, show each offer under its corresponding merchant.
28. Do not associate an offer or redemption with a different merchant.
29. If results belong to different merchants, keep their information separate.
30. Answer ONLY the question below. Answer each part of it,
    one by one, using the matching result.
31. If no result covers a part of the question (for example
    "current deals" but there are no offer results), say you
    could not find that information.
32. Redemption facts (whether, where, when) come ONLY from the
    RedemptionAgent result. If it says no redemptions were
    found, say exactly that. Never mention a redemption date
    that is not in the result.
33. Never add information the question did not ask for.
34.  Start directly with the answer. Never mention agents, sections,
   databases or "results". Keep it short and friendly.
35. Every record under OFFERS AND DEALS is a current deal.
    Describe its "offer", "offer_details" and "offer_valid_until".
    
36. Redemptions always belong to the logged-in user. Never
    describe them as another person's, even if the question
    names someone else.


USER'S QUESTION: {message}
(Same question with names resolved: {rewritten})

Answer every part of the question above, one by one.

"""
    try:
     # 6. Call LLM
        llm_response = await call_llm(llm, prompt, name="response")

        answer = (llm_response.content or "").strip()
    except LLMBusyError:
        raise  # chat.py returns 503 so the app can retry
    except Exception:
        logger.exception("response_llm_failed using_fallback=true")
        return {
            "response": fallback,
            "errors": ["response_llm_failed"],
        }


    # 7. Return response to graph state
    return {
        "response": answer or fallback}
    
    
    
