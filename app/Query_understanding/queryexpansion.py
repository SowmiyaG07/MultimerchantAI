
"""
query_expansion.py

Independent query-understanding and expansion module
for a Merchant Assistant.

Responsibilities:
1. Understand user intent.
2. Detect follow-up references.
3. Rewrite follow-up questions.
4. Decompose multi-intent questions.
5. Expand queries conservatively.
6. Select specialist agents.
7. Return a structured query plan.

This module does NOT execute database tools or call agents.
The supervisor is responsible for execution.
"""

from __future__ import annotations

import re
from dataclasses import dataclass,field

from typing import Any, Optional




# ============================================================
# 1. CONFIGURATION
# ============================================================





INTENT_TO_AGENT  = {
    "merchant": "merchant_agent",
    "offer": "offer_agent",
    "nearby": "nearby_agent",
    "redemption": "redemption_agent",
}

VALID_AGENTS = set(INTENT_TO_AGENT.values())

# Terms that suggest the user is asking about your business.
# Adjust these according to the actual capabilities of your app.
BUSINESS_TERMS = {
    "merchant",
    "merchants",
    "brand",
    "brands",
    "restaurant",
    "restaurants",
    "store",
    "stores",
    "shop",
    "shops",
    "offer",
    "offers",
    "discount",
    "discounts",
    "deal",
    "deals",
    "redeem",
    "redeemed",
    "redemption",
    "redemptions",
    "transaction",
    "transactions",
    "cashback",
    "gift card",
    "gift cards",
    "nearby",
    "near me",
    "closest",
    "location",
    "locations",
}


# Expand these terms only when the user's intent supports it.
# Expansion is deliberately conservative.
EXPANSION_TERMS = {
    "merchant": [],
    "offer": ["promotion", "deal"],
    "nearby": ["nearby location"],
    "redemption": [],
}

AGENT_TO_INTENT = {
    agent: intent for intent, agent in INTENT_TO_AGENT.items()
}

FOLLOW_UP_PATTERNS = [
    r"\bit\b",
    r"\bits\b",
    r"\bthey\b",
    r"\bthem\b",
    r"\btheir\b",
    # "that", "this", "those", "these" and "there" count only when
    # they point back to something, so "offers that are above 20%"
    # and "is there a discount" are not follow-ups.
    (
        r"\b(?:that|this|those|these) (?:one|ones|restaurant|restaurants"
        r"|merchant|merchants|store|stores|shop|shops|place|places"
        r"|brand|brands|offer|offers|deal|deals)\b"
    ),
    r"\b(?:from|at|near|in|to) there\b",
    r"\bwhat about\b",
    r"\bhow about\b",
    r"\bthe (?:first|second|third|last) one\b",
    r"\bmore about\b",
    r"\bshow me more\b",
]




@dataclass
class QueryPlan:
    """
    Final plan prepared for routing to the business agents.
    """

    original_query: str

    standalone_query: str = ""

    business_related: bool = False

    needs_history: bool = False

    needs_clarification: bool = False

    clarification_question: str | None = None

    selected_agents: list[str] = field(default_factory=list)

    expanded_queries: list[str] = field(default_factory=list)

# ============================================================
# 3. Text normalization
# ============================================================ 

def normalize_text(text: str | None) -> str:
    """
    Normalize whitespace without changing the meaning.
    """

    if not isinstance(text, str):
        return ""

    return re.sub(r"\s+", " ", text).strip()





def is_follow_up(message : str) -> bool :
    """
    Detect whether a question may depend on earlier context.

    This is a heuristic, not an intent classifier.
    """
    text = normalize_text(message).lower()
    for pattern in FOLLOW_UP_PATTERNS:
        if re.search(pattern,text):
            return True
    
    return False

# ============================================================
# 4. Agent validation
# ============================================================
    
    
def validate_selected_agents(selected_agents:list[str] | None,) ->list[str]:
    """
    Validate the agent names returned by the Supervisor LLM.

    This function does not select new agents.
    It removes unsupported names and duplicates.
    """
    
    if not selected_agents:
        return []
    result =[]
    for agent in selected_agents:
        if( isinstance (agent,str) and agent in VALID_AGENTS and agent not in result):
            result.append(agent)
    return result
    
    
# ============================================================
# 5. Conversation history helpers
# ============================================================




def conversation(history: Any) -> list[tuple[str, str]]:
    """
    Convert history into (question, answer) pairs.

    Expected history order (chronological, as stored in
    Redis and returned by load_relevant_history_from_sql):
        [Q_old, A_old, ..., Q_new, A_new]

    The returned pairs are newest first, so result[0]
    is always the latest conversation.
    """

    if not isinstance(history, list):
        return []

    pairs: list[list[str]] = []

    for item in history:
        if not isinstance(item, dict):
            continue

        role = item.get("role")
        content = normalize_text(item.get("content"))

        if not content:
            continue

        if role == "user":
            pairs.append([content, ""])

        elif role == "assistant":
            if pairs and not pairs[-1][1]:
                pairs[-1][1] = content
            else:
                pairs.append(["", content])

    result = []

    # Pairs were built oldest first; return them newest first.
    for question, answer in reversed(pairs):
        result.append((question, answer))

    return result

def history_is_sufficient(
    message: str,
    history: list[dict],
) -> bool:
    """
    Decide whether the cached history can answer the message,
    or whether the SQL fallback should be used.

    The history is sufficient only when the latest conversation
    has both a question and an answer. An unanswered latest
    question means the cache is incomplete.
    """
    pairs = conversation(history)

    if not pairs:
        return False

    latest_question, latest_answer = pairs[0]

    return bool(latest_question and latest_answer)
                
    
def format_history_for_prompt(history: Any) -> str:
    """
    Format history for the Supervisor LLM prompt.

    The latest conversation is written last, so the LLM reads
    the conversation in the order it happened.
    """
    lines = []

    for question, answer in reversed(conversation(history)):
        if question:
            lines.append(f"User: {question}")
        if answer:
            lines.append(f"Assistant: {answer}")

    return "\n".join(lines)


# ============================================================
# 6. Follow-up resolution
# ============================================================

def resolve_follow_up(
    message: str,
    rewritten_query: str | None,
    history: Any,
) -> tuple[str, bool, bool, str | None]:
    """
    Check the Supervisor's rewrite of a follow-up question.

    Returns:
        rewritten_query
        needs_history
        needs_clarification
        clarification_question

    Important:
        This function does not fetch history or call the LLM.
        SupervisorAgent loads the history and asks the LLM to
        rewrite the follow-up using it. This function only
        verifies that the rewrite no longer depends on context.
    """

    message = normalize_text(message)

    if not is_follow_up(message):
        return message, False, False, None

    if not conversation(history):
        return (
            message,
            True,
            True,
            "Could you clarify which previous result you mean?",
        )

    rewritten = normalize_text(rewritten_query)

    # Do not guess when the LLM could not replace the reference,
    # for example "the first one" when the latest answer had no list.
    if not rewritten or is_follow_up(rewritten):
        return (
            message,
            True,
            True,
            "Which merchant from the previous results are you referring to?",
        )

    return rewritten, True, False, None


# ============================================================
# 7. Search-query expansion
# ============================================================

def expand_query(
    query: str,
    selected_agents: list[str],
) -> list[str]:
    """
    Generate conservative search variants from EXPANSION_TERMS.

    Redemption:
        Keep the customer-specific query unchanged.

    Other searches:
        Add the expansion terms of each selected agent's intent,
        skipping words the query already contains.
    """

    # Redemption queries should retain their original meaning.
    if "redemption_agent" in selected_agents:
        return [query]

    variants = [query]
    query_words = set(query.lower().split())

    for agent in selected_agents:
        intent = AGENT_TO_INTENT.get(agent)

        for term in EXPANSION_TERMS.get(intent, []):
            missing = [
                word for word in term.split()
                if word.lower() not in query_words
            ]

            if missing:
                variants.append(f"{query} {' '.join(missing)}")

    # Preserve order and remove duplicates.
    return list(dict.fromkeys(variants))


# ============================================================
# 8. Build the final query plan
# ============================================================   
def build_query_plan(
    message: str,
    decision: Any,
    chat_history: Any = None,
) -> QueryPlan:
    """  
    Combine the Supervisor LLM decision with deterministic
    query processing.

    Expected decision fields:
        business_related
        history_needed
        selected_agent
        rewritten_query
        expanded_queries
        needs_clarification
        clarification_question

    This function does not call the LLM and does not access
    Redis or SQL.
    """
    
    
    message = normalize_text(message)

    plan = QueryPlan(
        original_query=message,
    )

     # 2. Read the Supervisor's business-relatedness decision.
    plan.business_related = bool(
        getattr(decision, "business_related", False)
    )

    if not plan.business_related:
        return plan

    # 3. Copy the history requirement decided by the Supervisor.
    plan.needs_history = bool(
        getattr(decision, "history_needed", False)
    )
    
      # 4. Respect the Supervisor's clarification decision.
    if getattr(decision, "needs_clarification", False):
        plan.needs_clarification = True
        plan.clarification_question = (
            getattr(decision, "clarification_question", None)
            or "Could you clarify your question?"
        )
        return plan

    # 5. Validate the selected agent names.
    plan.selected_agents = validate_selected_agents(
        getattr(decision, "selected_agent", None)
    )

    if not plan.selected_agents:
        plan.needs_clarification = True
        plan.clarification_question = (
            "Are you looking for a merchant, an offer, "
            "a nearby restaurant, or redemption history?"
        )
        return plan
    
    
    # 6. Resolve follow-ups only if the question actually looks
    #    context-dependent. History retrieval and the LLM rewrite
    #    with that history have already happened in SupervisorAgent.
    if is_follow_up(message):
        rewritten, history_needed, needs_clarification, question = (
            resolve_follow_up(
                message=message,
                rewritten_query=getattr(decision, "rewritten_query", ""),
                history=chat_history,
            )
        )

        plan.needs_history = (
            plan.needs_history or history_needed
        )

        if needs_clarification:
            plan.needs_clarification = True
            plan.clarification_question = (
                question
                or "Could you clarify which previous result you mean?"
            )
            return plan

        plan.standalone_query = rewritten

    else:
        # For a self-contained query, trust the Supervisor's rewrite.
        plan.standalone_query = normalize_text(
            getattr(decision, "rewritten_query", "")
        ) or message

    # 7. Use the LLM's expanded queries when supplied.
    llm_expanded_queries = getattr(
        decision,
        "expanded_queries",
        None,
    )

    if llm_expanded_queries:
        cleaned_queries = [
            normalize_text(query)
            for query in llm_expanded_queries
            if isinstance(query, str) and normalize_text(query)
        ]

        # Always include the main standalone query.
        plan.expanded_queries = list(
            dict.fromkeys(
                [plan.standalone_query, *cleaned_queries]
            )
        )

    else:
        # Otherwise, use the conservative Python expansion rules.
        plan.expanded_queries = expand_query(
            query=plan.standalone_query,
            selected_agents=plan.selected_agents,
        )

    return plan



    
    