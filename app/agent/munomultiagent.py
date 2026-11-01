
from typing import TypedDict, Literal, Any
from datetime import date
from app.agent.graph.state import Agentstate
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph, START, END
import os
from pydantic import Field, BaseModel


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

ollama_model = os.getenv("OLLAMA_MODEL")

ollama_base_url = os.getenv("OLLAMA_BASE_URL")


llm = ChatOllama(model=ollama_model, temperature=0, num_ctx=8192,
                 base_url=ollama_base_url, num_predict=1024)


class SupervisorDecision(BaseModel):
    business_related: bool
    history_needed: bool
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
    print("USER QUESTION:", message)
    if not message:
        return {
            "selected_agent": [],
            "response": "Please enter a question"
        }
    session_id = state.get("session_id")
    try:
        decision = await decide_supervisor(message)
    except Exception:
        logger.exception("supervisor_llm_failed")
        return {
            "selected_agent": [],
            "response": SUPERVISOR_ERROR_MESSAGE,
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
                try:
                    decision = await decide_supervisor(message, history)
                except Exception:
                    logger.exception("supervisor_llm_failed with_history=true")
                    return {
                        "selected_agent": [],
                        "response": SUPERVISOR_ERROR_MESSAGE,
                    }
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

    logger.info(
        "supervisor_routed agents=%s needs_history=%s history_messages=%d",
        plan.selected_agents, plan.needs_history, len(history),
    )

    print("SELECTED AGENT:", plan.selected_agents)

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
        "redemption_agent_params": [
            item.model_dump()
            for item in decision.redemption_search_params
        ],

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
  
IMPORTANT: IMPLICIT MERCHANT REFERENCES

A question can be business-related even when the merchant name
is not explicitly mentioned.

The following expressions indicate possible references to
a previously discussed merchant:

- the restaurant
- the restaurant's
- that restaurant
- its
- their
- the merchant
- the merchant's
- that store
- the first one

Examples:

Example 1:
User: What is the full address of Dreamland BBQ?
Assistant: Dreamland BBQ is located in Alabama.

User: What is the restaurant's contact phone number?

Expected decision:
business_related = true
history_needed = true

Example 2:
User: What is the restaurant's website?

Expected decision:
business_related = true
history_needed = true

Example 3:
User: What is Dreamland BBQ's website?

Expected decision:
business_related = true
history_needed = false

Example 4:
User: What is Python?

Expected decision:
business_related = false
history_needed = false

IMPORTANT:
- Restaurant contact details, phone numbers, addresses,
  websites and business information are business-related.
- Do not classify a restaurant information request as
  unrelated simply because the merchant name is missing.
- If the merchant is not explicitly mentioned but an implicit
  reference exists, set history_needed = true.

AGENT SELECTION RULES

- Select only agents relevant to the user's intent.
- Multiple agents may be selected when a request requires
  multiple kinds of information.
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
when merchant_agent is selected and the user provides a
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

4. REDEMPTION AGENT

Populate redemption_search_params whenever redemption_agent
is selected for the user's own redemption-history request.

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

Example:
User: "Show my redemption history"

Expected:
{{
    "selected_agent": ["redemption_agent"],
    "redemption_search_params": [
        {{}}
    ]
}}

Example (if today's date were 2026-01-15):
User: "Show my redemptions from the last 7 days"

Expected:
{{
    "selected_agent": ["redemption_agent"],
    "redemption_search_params": [
        {{
            "from_date": "2026-01-08",
            "to_date": "2026-01-15"
        }}
    ]
}}

Example:
User: "Show my redemptions at Dreamland BBQ"

Expected:
{{
    "selected_agent": ["redemption_agent"],
    "redemption_search_params": [
        {{"merchant_name": "Dreamland BBQ"}}
    ]
}}
IMPORTANT VALIDATION RULES

- Each selected agent must have its corresponding parameter
  list populated when a usable criterion is available.
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

    decision = await supervised_llm.ainvoke(prompt)
    
    return decision
