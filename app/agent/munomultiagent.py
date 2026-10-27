
from typing import TypedDict,Literal,Any
from datetime import date
from app.agent.graph.state import Agentstate
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph,START,END
import os
from pydantic import Field,BaseModel


from app.agent.models.search_params import MearchantSearchparams,OfferSearchParams,NearbySearchParams,RedemptionSearchParams

from app.cache.redis_cache import load_history_from_redis,save_history_to_redis

from app.services.history_service import load_relevant_history_from_sql

from app.Query_understanding.queryexpansion import build_query_plan,history_is_sufficient,format_history_for_prompt



ollama_model = os.getenv("OLLAMA_MODEL")

ollama_base_url = os.getenv("OLLAMA_BASE_URL")


llm = ChatOllama(model=ollama_model,temperature = 0, num_ctx=8192,base_url=ollama_base_url,num_predict=256)



    

class SupervisorDecision(BaseModel):
    business_related : bool
    history_needed : bool
    selected_agent : list[
        Literal[
            "merchant_agent",
            "offer_agent",
            "nearby_agent",
            "redemption_agent",
            
        ]
    ]
    rewritten_query : str
    expanded_queries : list[str ] = Field(default_factory = list)
    
    merchant_name: str | None = None
     # Merchant tool arguments
    merchant_search_params : list[MearchantSearchparams] = Field (default_factory=list)
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
    needs_clarification : bool = False
    clarification_question : str | None = None
    
    
supervised_llm = llm.with_structured_output(SupervisorDecision)  

    
    
async def SupervisorAgent(state:Agentstate):
    message = state.get("message","").strip()
    if not message:
            return {
                "selected_agent": [],
                "response": "Please enter a question"
            }
    session_id = state.get("session_id")
    decision = await decide_supervisor(message)
    
       # 3. Reject requests outside the business domain.
    if not decision.business_related:
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
            decision = await decide_supervisor(message, history)

    # 5. Build the query plan using the LLM decision
    #    and conversation history.
    plan = build_query_plan(
        message=message,
        decision=decision,
        chat_history=history,
        
    )

    # 6. Stop and ask the user if clarification is needed.
    if plan.needs_clarification:
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
        "merchant_search_params": [
            item.model_dump()
            for item in decision.merchant_search_params
        ],
        "offer_search_params": [
                    item.model_dump()
                    for item in decision.offer_search_params
                ],
        "nearby_agent_params": [
                    item.model_dump()
                    for item in decision.nearby_search_params
                ],
        "redemption_agent_params": [
                    item.model_dump()
                    for item in decision.redemption_search_params
                ],
        
        "response": "",
    }
  

    


async def decide_supervisor(message, history=None) -> SupervisorDecision :

    history_section = ""

    if history:
        history_section = f"""
    Previous conversation (oldest first, latest last):
    {format_history_for_prompt(history)}

    Use the latest conversation to replace references such as
    "it", "they", "that restaurant" or "the first one" with the
    merchant name in rewritten_query. If you cannot tell which
    merchant is meant, set needs_clarification to true and ask
    which merchant the user means.
    """

    prompt = f"""
    You are the supervisor for an AI Merchant Assistant.

    Analyze the user's message and decide:

    1. Is the question related to merchants, offers,
       nearby restaurants, or redemption history?
    2. Does answering require previous conversation history?
    3. Which agent or agents should handle the request?
    4. Rewrite the user's query clearly.
    5. Provide expanded search queries when appropriate.
    6. Ask for clarification if necessary.

    Available agents:
    - merchant_agent
    - offer_agent
    - nearby_agent
    - redemption_agent
    7. Identify the merchant name from the current message
   or conversation history.
   - Do not invent merchant names.
   - If the user asks "What offers does it have?",
     resolve "it" using the conversation history.
   - Return the resolved name in merchant_name.
   - If the merchant cannot be identified, ask for clarification.

    Important:
    - Do not assume missing conversation context.
    - If the question refers to "it", "that restaurant",
      "the first one", or similar references, set
      history_needed to true when context is required.
    - Do not classify unrelated questions as business-related.
    - Return a structured response matching the schema.
    
    8. Extract search parameters for the selected agents.

- merchant_search_params:
  Extract attributes accepted by the merchant search tool,
  such as name, category, city, state, country, address,
  neighborhood, description, latitude, longitude, keyword,
  and limit.

- offer_search_params:
  Extract attributes accepted by the offer search tool.
  Preserve the merchant name when searching for a specific
  merchant's offers.

- nearby_search_params:
  Extract the location and filters accepted by the nearby
  restaurant search tool. Use supplied coordinates when available.
  Never invent coordinates.

- redemption_search_params:
  Extract the customer and date filters accepted by the
  redemption search tool.

Rules:
- Populate parameters only for selected agents.
- Return an empty list for agents that are not selected.
- Do not invent missing values.
- Use the exact parameter names defined in each schema.
- If a required value is missing, ask for clarification.
    {history_section}
     User message: {message}
    """

    decision = await supervised_llm.ainvoke(prompt)
    return decision



def Response_agent(state: Agentstate):
    print("Response Agent: Combining results")

    results = [
        state.get('merchant_results',''),
        state.get('offer_results',''),
        state.get('nearby_merchantresults',''),
        state.get('redemption_results',''),
    ]

    final_response = "\n".join(
        [f"User query: {state.get('message','')}"]
        + [result for result in results if result]
    )

    return {"response": final_response}
    
    

    

    




    
