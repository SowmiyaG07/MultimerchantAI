

import json

from app.agent.graph.state import Agentstate

from app.agent.munomultiagent import llm
from app.utilities.log import Applogger
from app.agent.llm_limits import call_llm

logger = Applogger.get_loggerdata(__name__)


SECTION_TITLES = {
    "MerchantAgent": "MERCHANT DETAILS",
    "OfferAgent": "OFFERS AND DEALS",
    "NearbyAgent": "DISTANCE AND NEARBY PLACES",
    "RedemptionAgent": "THE USER'S REDEMPTIONS",
}

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

    # Shown if the LLM fails
    fallback = "\n\n".join(
        str(item["results"]) for item in all_agent_results
    )

    
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
    except Exception:
        logger.exception("response_llm_failed using_fallback=true")
        answer = ""


    # 7. Return response to graph state
    return {
        "response": answer or fallback}
    
    
    
