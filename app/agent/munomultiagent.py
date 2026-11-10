
from typing import TypedDict, Literal, Any
from datetime import date
from app.agent.graph.state import Agentstate
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph, START, END
import os
from pydantic import Field, BaseModel
from app.agent.llm_limits import call_llm, LLMBusyError


from app.agent.models.search_params import MearchantSearchparams, OfferSearchParams, NearbySearchParams, RedemptionSearchParams

from app.cache.redis_cache import load_history_from_redis, save_history_to_redis

from app.services.history_service import load_relevant_history_from_sql

from app.Query_understanding.queryexpansion import build_query_plan, history_is_sufficient, format_history_for_prompt
from app.utilities.log import Applogger

logger = Applogger.get_loggerdata(__name__)


# Shown when the supervisor's LLM call fails.
SUPERVISOR_ERROR_MESSAGE = (
    "Sorry, I can't answer that right now. Please try again in a moment."
)

# Shown when the user asks for another customer's data.
OTHER_CUSTOMER_MESSAGE = (
    "For privacy, I can only show your own redemption history. "
    "I can't access another customer's account."
)


ollama_model = os.getenv("OLLAMA_MODEL")

ollama_base_url = os.getenv("OLLAMA_BASE_URL")


llm = ChatOllama(model=ollama_model, temperature=0, num_ctx=8192,
                 base_url=ollama_base_url, num_predict=1024)


class SupervisorDecision(BaseModel):
    business_related: bool
    history_needed: bool
    other_customer_requested: bool
    selected_agent: list[
        Literal[
            "merchant_agent",
            "offer_agent",
            "nearby_agent",
            "redemption_agent",

        ]
    ]
    rewritten_query: str
    expanded_queries: list[str] = Field(default_factory=list)

    merchant_name: str | None = None
    # Merchant tool arguments
    merchant_search_params: list[MearchantSearchparams] = Field(
        default_factory=list)
    # Offer tool arguments
    offer_search_params: list[OfferSearchParams] = Field(
        default_factory=list
    )
    # Nearby tool arguments
    nearby_search_params: list[NearbySearchParams] = Field(
        default_factory=list
    )

    # Redemption tool arguments
    redemption_search_params: list[RedemptionSearchParams] = Field(
        default_factory=list
    )
    needs_clarification: bool = False
    clarification_question: str | None = None


supervised_llm = llm.with_structured_output(SupervisorDecision)


async def SupervisorAgent(state: Agentstate):
    message = state.get("message", "").strip()
    if not message:
        return {
            "selected_agent": [],
            "response": "Please enter a question"
        }
    session_id = state.get("session_id")
    try:
        decision = await decide_supervisor(message)
    except LLMBusyError:
        raise  # chat.py returns 503 so the app can retry
    except Exception:
        logger.exception("supervisor_llm_failed")
        return {
            "selected_agent": [],
            "response": SUPERVISOR_ERROR_MESSAGE,
            "errors": ["supervisor_llm_failed"],
        }
    if decision.other_customer_requested:
        logger.info("supervisor_rejected reason=other_customer_requested")
        return {
            "selected_agent": [],
            "business_related": True,
            "needs_history": False,
            "needs_clarification": False,
            "response": OTHER_CUSTOMER_MESSAGE,
        }

    history = []

    if decision.history_needed:
        history = await load_history_from_redis(session_id)

        if not history or not history_is_sufficient(message, history):
            history = await load_relevant_history_from_sql(
                session_id=session_id,
                message=message,
                max_tokens=1500,
            )

        if history:
            await save_history_to_redis(
                session_id=session_id,
                history=history,
                ttl_seconds=1800,
            )

            # The first decision was made without history, so it could
            # not resolve "it", "the first one", etc. Decide again with
            # the history so rewritten_query becomes a standalone query.
        if history:
            first_agents = decision.selected_agent

            try:
                decision = await decide_supervisor(message, history)
            except LLMBusyError:
                raise
            except Exception:
                logger.exception("supervisor_llm_failed with_history=true")
                return {
                    "selected_agent": [],
                    "response": SUPERVISOR_ERROR_MESSAGE,
                    "errors": ["supervisor_llm_failed"],
                }
            # The history call resolves references only; it must not
            # drop agents the first call already selected.
            decision.selected_agent = list(dict.fromkeys(
                first_agents + decision.selected_agent
            ))

   # 3. Reject requests outside the business domain.
    if not decision.business_related:
        logger.info("supervisor_rejected reason=not_business_related")
        return {
            "selected_agent": [],
            "business_related": False,
            "needs_history": False,
            "needs_clarification": False,
            "response": (
                "I can help with merchants, offers, "
                "nearby restaurants, and redemption history."
            ),
        }
     # 4. Load conversation history only when needed.

    # 5. Build the query plan using the LLM decision
    #    and conversation history.
    plan = build_query_plan(
        message=message,
        decision=decision,
        chat_history=history,

    )

    # 6. Stop and ask the user if clarification is needed.
    if plan.needs_clarification:
        logger.info("supervisor_clarification needs_history=%s",
                    plan.needs_history)
        return {
            "selected_agent": [],
            "business_related": plan.business_related,
            "needs_history": plan.needs_history,
            "needs_clarification": True,
            "chat_history": history,
            "response": (
                plan.clarification_question
                or "Could you clarify your question?"
            ),
        }

    # Small models often set merchant_name but leave
    # merchant_search_params empty. Fall back to searching by name
    # so the merchant agent still has a criterion.

    merchant_params = []
    for item in decision.merchant_search_params:
        merchant_params.append(item.model_dump())

    if "merchant_agent" in plan.selected_agents:
        if not merchant_params:
            if decision.merchant_name:
                merchant_params = [
                    {"name": decision.merchant_name}

                ]
    offer_params = []
    for item in decision.offer_search_params:
        offer_params.append(item.model_dump())


# Step 4: Check whether offer parameters are missing
    if "offer_agent" in plan.selected_agents:
        if not offer_params:
            if decision.merchant_name:
                offer_params = [
                    {"name": decision.merchant_name}
                ]

    nearby_params = []
    for item in decision.nearby_search_params:
        nearby_params.append(item.model_dump())

    # "How far is it?" -> the model may set merchant_name only.
    if "nearby_agent" in plan.selected_agents:
        if not nearby_params:
            if decision.merchant_name:
                nearby_params = [
                    {"merchant_name": decision.merchant_name}
                ]
    redemption_params = []
    for item in decision.redemption_search_params:
        redemption_params.append(item.model_dump())
    if "redemption_agent" in plan.selected_agents:
        if not redemption_params:
            if decision.merchant_name:
                redemption_params = [
                    {"merchant_name": decision.merchant_name}
                ]
            else:
                redemption_params = [{}]

    logger.info(
        "supervisor_routed agents=%s needs_history=%s history_messages=%d",
        plan.selected_agents, plan.needs_history, len(history),
    )


    # 7. Return the query plan to the LangGraph workflow.
    return {
        "selected_agent": plan.selected_agents,
        "expanded_query": plan.standalone_query,
        "expanded_queries": plan.expanded_queries,
        "business_related": plan.business_related,
        "needs_history": plan.needs_history,
        "needs_clarification": False,
        "chat_history": history,
        "merchant_name": decision.merchant_name,
        "merchant_search_params": merchant_params,
        "offer_search_params": offer_params,
        # Decision fields *_search_params map to the state keys
        # nearby_agent_params / redemption_agent_params.
        "nearby_agent_params": nearby_params,
        "redemption_agent_params": redemption_params,


        "response": "",
    }


async def decide_supervisor(message, history=None) -> SupervisorDecision:

    history_section = ""

    if history:
        history_section = f"""
    Previous conversation (oldest first, latest last):
    {format_history_for_prompt(history)}

    History resolution rules:
- Use conversation history to resolve references such as "it",
  "they", "that restaurant", "the first one", and "those offers".
- Rewrite the query using the resolved merchant, offer, or
  restaurant name in rewritten_query.
- Return the resolved merchant name in merchant_name when
  a merchant can be identified.
- Set history_needed to true when previous conversation
  context is required.
- If the reference cannot be resolved confidently, set
  needs_clarification to true and explain what needs
  clarification.
"""

    prompt = f"""
    You are the supervisor for an AI Merchant Assistant.
    
    FIRST, PRIVACY CHECK (most important rule):
    The user may only see THEIR OWN redemption history. If the message asks
    for another person's or another customer's redemptions or data - by
    name ("Priya's"), by customer id ("customer 20817", "my id is 20817")
    or by relationship ("my brother") - set other_customer_requested = true.
    Do not ask for clarification in that case.


    Today's date is {date.today().isoformat()}. Use it to resolve
    relative dates such as "today", "last week" or "last 7 days".

    Analyze the user's message and decide:

    1. Is the question related to merchants, offers,
       nearby restaurants, or redemption history?
    2. Does answering require previous conversation history?
    3. Which agent or agents should handle the request?
    4. Rewrite the user's query clearly.
    5. Provide expanded search queries when appropriate.
    6. Ask for clarification if necessary.
    7. Extract search parameters for every selected agent.

    Available agents:
    - merchant_agent
    - offer_agent
    - nearby_agent
    - redemption_agent
    8. Identify the merchant name from the current message
   or conversation history.
   - Do not invent merchant names.
   - If the user asks "What offers does it have?",
     resolve "it" using the conversation history.
   - Return the resolved name in merchant_name.
   - If the merchant cannot be identified from the available
  context and is necessary to answer, request clarification.
  


FOLLOW-UP DETECTION (history_needed)

You do NOT see the previous conversation here. Decide only
from the wording whether the question depends on it.

Set history_needed = true when the question:
- uses a reference instead of a name: it, its, they, them,
  their, there, that/this/the restaurant, that offer,
  that place, that transaction, the first one, the gym one
- continues an earlier topic: "and the month before?",
  "what about Saturday?", "tell me more"
- is incomplete on its own: "What if I only order one drink?",
  "Can I use it on a Sunday?"

Set history_needed = false when the question names everything
it needs, even if it contains "it" or "they" about something
named in the same sentence:
- "How long would it take to walk to Chris' Famous Hotdogs?"
- "Which places have I redeemed at, and do any of them have deals?"




IMPORTANT:
- A follow-up question is business_related = true. Never mark
  a question unrelated just because it is short or unclear;
  if it could refer to an earlier merchant, offer, location or
  redemption, set history_needed = true instead.
- Restaurant contact details, phone numbers, addresses,
  websites and business information are business-related.

Examples:
User: "Do they have any social media pages?"
-> business_related = true, history_needed = true

User: "Can I use it on a Sunday?"
-> business_related = true, history_needed = true

User: "What is Dreamland BBQ's website?"
-> business_related = true, history_needed = false

User: "What is Python?"
-> business_related = false, history_needed = false

AGENT SELECTION RULES

- merchant_agent: details of a merchant (address, city, ZIP,
  neighborhood, phone, cuisine, category, description, social
  media) and searches like "a BBQ place in Tuscaloosa" or
  "a hair salon in Atlanta".
- offer_agent: offers, current deals, discounts, offer rules
  (weekends, takeout, expiry, download limits) and HOW to
  redeem an offer.
- nearby_agent: ONLY relative to the user's own location:
  "near me", "nearby", "closest", "around here", "within 5
  miles", "how far is X", "walk/drive/bike to X".
- redemption_agent: ONLY the user's OWN past redemptions and
  transactions ("my redemptions", "did I redeem", "last month").
- Select more than one agent only when the user clearly asks
  for more than one kind of information:
  "restaurants near me with offers" -> offer_agent, nearby_agent
  "Pizzeria Bianco's phone and its deal" -> merchant_agent, offer_agent
- Do NOT add merchant_agent to an offer question just because
  it names a merchant: " current deals at Dreamland BBQ" -> offer_agent.
- Do not classify unrelated questions as business-related.
- Return a structured response matching the supplied schema.


SEARCH PARAMETER RULES

General:
- Populate the corresponding parameter list whenever its
  agent is selected and a usable search criterion is available.
- Do not leave a parameter list empty if the relevant
  criterion is already present in the message or history.
- Extract parameters only for selected agents.
- Return an empty list for agents that are not selected.
- Use the exact field names accepted by each Pydantic schema.
- Do not invent missing values.
- Do not put parameters for one agent into another agent's list.
- If a usable criterion is available, populate the parameter
  list even when other optional filters are missing.
- If the request genuinely lacks information required to
  proceed, set needs_clarification to true as appropriate.

1. MERCHANT AGENT

Populate merchant_search_params with at least one parameter
when merchant_agent is selected and theFOLLOW-UP DETECTION (history_needed)

You do NOT see the previous conversation here. Decide only
from the wording whether the question depends on it.

Set history_needed = true when the question:
- uses a reference instead of a name: it, its, they, them,
  their, there, that/this/the restaurant, that offer,
  that place, that transaction, the first one, the gym one
- continues an earlier topic: "and the month before?",
  "what about Saturday?", "tell me more"
- is incomplete on its own: "What if I only order one drink?",
  "Can I use it on a Sunday?"

Set history_needed = false when the question names everything
it needs, even if it contains "it" or "they" about something
named in the same sentence:
- "How long would it take to walk to Chris' Famous Hotdogs?"
- "Which places have I redeemed at, and do any of them have deals?" deals means need to check offers

IMPORTANT:
- A follow-up question is business_related = true. Never mark
  a question unrelated just because it is short or unclear;
  if it could refer to an earlier merchant, offer, location or
  redemption, set history_needed = true instead.
- Restaurant contact details, phone numbers, addresses,
  websites and business information are business-related.

Examples:
User: "Do they have any social media pages?"
-> business_related = true, history_needed = true

User: "Can I use it on a Sunday?"
-> business_related = true, history_needed = true

User: "What is Dreamland BBQ's website?"
-> business_related = true, history_needed = false




User: "What is Python?"
-> business_related = false, history_needed = false
 user provides a
merchant name or another usable merchant-search criterion.

Extract only fields supported by the merchant-search schema,
such as name, category, city, state, country, address,
neighborhood, description, keyword, and limit.

Do not include latitude or longitude unless the merchant
search schema and tool explicitly require them.

Preserve the identified merchant name in merchant_name.

Example:
User: "Explain Dreamland BBQ"

Expected:
{{
    "selected_agent": ["merchant_agent"],
    "merchant_name": "Dreamland BBQ",
    "merchant_search_params": [
        {{"name": "Dreamland BBQ"}}
    ]
}}

Example:
User: "Find Italian restaurants in Atlanta"

Expected:
{{
    "selected_agent": ["merchant_agent"],
    "merchant_search_params": [
        {{
            "category": "restaurant",
            "city": "Atlanta",
            "keyword": "Italian"
        }}
    ]
}}

Examples
 What is the address of Cafe De Monde?
 Directly asking address Expected is full address of that merchant


2. OFFER AGENT

Populate offer_search_params whenever offer_agent is selected
and the user provides a usable offer-related criterion.

Extract supported fields: name (the merchant name), keyword,
city, state, offer_type, min_percent, max_percent and limit.

When searching for a specific merchant's offers, preserve
the merchant name in the offer parameters.

Example:
User: "Show me pizza offers"

Expected:
{{
    "selected_agent": ["offer_agent"],
    "offer_search_params": [
        {{"keyword": "pizza"}}
    ]
}}

Example:
User: "What offers does Dreamland BBQ have?"

Expected:
{{
    "selected_agent": ["offer_agent"],
    "merchant_name": "Dreamland BBQ",
    "offer_search_params": [
        {{"name": "Dreamland BBQ"}}
    ]
}}

Example with conversation history:
Previous conversation: The user was discussing Dreamland BBQ.
User: "What offers does it have?"

Expected:
{{
    "selected_agent": ["offer_agent"],
    "merchant_name": "Dreamland BBQ",
    "offer_search_params": [
        {{"name": "Dreamland BBQ"}}
    ],
    "history_needed": true
}}

Use the exact field names defined in the offer-search schema.
If it uses "keyword" rather than "keywords", use "keyword".

3. NEARBY AGENT

Select nearby_agent for two kinds of request:
a) Places near the user ("near me", "nearby", "within 3 miles").
   Fields: category, cuisine, keyword, radius_km or radius_miles, limit.
b) Distance or travel time to ONE named merchant ("how far is X",
   "how long to walk to X"). Fields: merchant_name, travel_mode
   (drive, walk or bike).

Use radius_miles when the user says miles, radius_km for km.

Do not extract latitude or longitude. The application supplies
the user's device location automatically.

Do not invent a location or claim the user's location is known
if it is not supplied by the application.

Example:
User: "Find restaurants near me"
c) Location details of ONE named merchant
Examples:
What is the location of Dreamland BBQ?
What are the latitude and longitude of this restaurant?
Give me the restaurant's coordinates.

Where is the restaurant located?

Fields:
merchant_name
requested_fields: requested location information, such as latitude, longitude, address, or all_location_details.
Expected:
{{
    "selected_agent": ["nearby_agent"],
    "nearby_search_params": [
        {{"category": "restaurant"}}
    ]
}}

Example:
User: "Find Italian restaurants nearby"

Expected:
{{
    "selected_agent": ["nearby_agent"],
    "nearby_search_params": [
        {{
            "category": "restaurant",
            "cuisine": "Italian"
        }}
    ]
}}

Example:
User: "Find restaurants within 5 km"

Expected:
{{
    "selected_agent": ["nearby_agent"],
    "nearby_search_params": [
        {{
            "category": "restaurant",
            "radius_km": 5
        }}
    ]
}}

Example:
User: "Pizza places within 3 miles"

Expected:
{{
    "selected_agent": ["nearby_agent"],
    "nearby_search_params": [
        {{"keyword": "pizza", "radius_miles": 3}}
    ]
}}

Example:
User: "How long does it take to walk to Dreamland BBQ?"

Expected:
{{
    "selected_agent": ["nearby_agent"],
    "merchant_name": "Dreamland BBQ",
    "nearby_search_params": [
        {{"merchant_name": "Dreamland BBQ", "travel_mode": "walk"}}
    ]
}}

Example:
User : Asking about location details latitude and longitude region details ?
Expected:
{{
    "selected_agent": ["nearby_agent"],
    "merchant_name": "Dreamland BBQ",
    "nearby_search_params": [
        {{"merchant_name": "Dreamland BBQ", "travel_mode": "walk"}}
    ]
}}




REDEMPTION AGENT

PURPOSE:
The redemption_agent retrieves the authenticated customer's
own redemption history from the database.

WHEN TO SELECT:
Select redemption_agent whenever the user asks to check,
verify, retrieve, or discuss their own previous redemptions.

Redemption intent can appear in questions, statements,
or combined requests.

Recognize all the following patterns:

- Have I redeemed this offer?
- Have I already used this offer?
- Did I use the Pizzeria Bianco offer?
- Have I used this merchant's offer before?
- When did I last redeem an offer?
- Show my redemption history.
- Check my previous redemptions.
- I redeemed at Van Michael Salon last month.
- I used the offer at this restaurant.
- Was this offer already redeemed by me?
- Can you check whether I have used this deal?

IMPORTANT INTENT RULES:

1. Do not require the user to explicitly say
   "check my redemption history".

2. Statements such as "I redeemed at Van Michael Salon
   last month" must be recognized as redemption-related
   when the user asks for information about that redemption
   or merchant.

3. If the user asks whether they have already used an offer,
   ALWAYS select redemption_agent.

4. If the user asks about redemption history AND other
   merchant, offer, or location information, select
   redemption_agent alongside all other required agents.

5. Do not select redemption_agent merely because the word
   "redeem" appears in a general offer description.
   The intent must concern the authenticated customer's
   own redemption history.

6. Do not let merchant_agent, offer_agent, or nearby_agent
   replace redemption_agent when redemption intent exists.

7. An explicitly mentioned merchant name must be extracted
   directly from the current user message. Do not ask for
   clarification when the merchant is identifiable.

8. Do not interpret a statement about a past redemption
   as merely background information when the user is asking
   for verification or related redemption information.

PARAMETER EXTRACTION:

Populate redemption_search_params whenever
redemption_agent is selected for the user's own
redemption-history request.

Extract only supported filters:
- from_date
- to_date
- merchant_name

Use YYYY-MM-DD for dates when they can be resolved.
Resolve relative dates from today's date given above.

Never extract or request a customer ID. The application
supplies the authenticated customer's identity automatically.

If no filters are required, use an empty parameter object
inside the list if permitted by the schema.

Examples:

Examples:

User: "Show my redemption history"

Expected:
{{
    "selected_agent": ["redemption_agent"],
    "redemption_search_params": [
        {{}}
    ]
}}

User: "Have I already used the Firestone Complete Auto Care
oil change offer?"

Expected:
{{
    "selected_agent": ["redemption_agent"],
    "redemption_search_params": [
        {{"merchant_name": "Firestone Complete Auto Care"}}
    ]
}}

User: "Did I redeem at Dreamland BBQ before? What is their
address and their other deals?"

Expected:
{{
    "selected_agent": ["merchant_agent", "offer_agent", "redemption_agent"],
    "merchant_name": "Dreamland BBQ",
    "merchant_search_params": [{{"name": "Dreamland BBQ"}}],
    "offer_search_params": [{{"name": "Dreamland BBQ"}}],
    "redemption_search_params": [
        {{"merchant_name": "Dreamland BBQ"}}
    ]
}}

User: "I redeemed at Cafe Du Monde last month. What's their
phone number and how far is it from me?"

Expected:
{{
    "selected_agent": ["merchant_agent", "nearby_agent", "redemption_agent"],
    "merchant_name": "Cafe Du Monde",
    "merchant_search_params": [{{"name": "Cafe Du Monde"}}],
    "nearby_search_params": [{{"merchant_name": "Cafe Du Monde"}}],
    "redemption_search_params": [
        {{"merchant_name": "Cafe Du Monde",
          "from_date": "<first day of last month>",
          "to_date": "<last day of last month>"}}
    ]
}}


IMPORTANT VALIDATION RULES

- When a user asks multiple independent questions in one
message, select ALL agents required to answer them.

- Do not select only the agent responsible for the
last question or the main subject.

- Each selected agent must have its corresponding
search parameters populated according to its schema.

- Do not omit redemption_agent when redemption intent
is detected, even if merchant_agent, offer_agent,
or nearby_agent are also required

- Each selected agent must have its corresponding parameter
  list populated when a usable criterion is available.
  When multiple agents are selected:
- Consider the results from every selected agent.
- Do not ignore successful results from any agent.
- For redemption queries, report whether the customer has redeemed the offer.
- For offer queries, report whether the offer exists and its validity.
- When both agents are selected, combine both findings into one answer.
- Never interpret an empty redemption result as an empty offer result.
- Unselected agents must have empty parameter lists.
- Parameter objects must match their corresponding Pydantic
  models and actual tool inputs.
- Do not return invented merchant names, locations, dates,
  coordinates, customer IDs, or other values.
- Resolve references from conversation history when possible.
- If a reference is ambiguous, request clarification instead
  of guessing.
- Do not fabricate required parameters merely to avoid an
  empty list.
- Return only the structured output required by the schema.
    {history_section}
     User message: {message}
"""

    decision = await call_llm(supervised_llm, prompt, name="supervisor")


    return decision
