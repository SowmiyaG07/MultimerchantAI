

import json
import os

from app.agent.graph.state import Agentstate

from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph,START,END


ollama_model = os.getenv("OLLAMA_MODEL")

ollama_base_url = os.getenv("OLLAMA_BASE_URL")

llm = ChatOllama(model=ollama_model,temperature = 0, num_ctx=8192,base_url=ollama_base_url,num_predict=1024)


def _format_result(result) -> str:
    """
    Agents return either a message string or a list of records
    (dicts). Turn either one into readable text.
    """
    if isinstance(result, str):
        return result

    if isinstance(result, list):
        blocks = []
        for record in result:
            if isinstance(record, dict):
                # Skip empty fields so the answer stays short.
                lines = [
                    f"{key}: {value}"
                    for key, value in record.items()
                    if value not in (None, "")
                ]
                blocks.append("\n".join(lines))
            else:
                blocks.append(str(record))
        return "\n\n".join(blocks)

    return str(result)


async def ResponseAgent(state: Agentstate) -> dict:
    # Step 1: Get results from each agent
    
     # 1. Current user message
    message = state.get("message", "")

    # 2. Previous conversation
    chat_history = state.get("chat_history", [])

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
    history_text = json.dumps(
        chat_history,
        indent=2,
        default=str
    )
    result_text = json.dumps(
        all_agent_results,
        indent=2,
        default = str
    )
    prompt = f"""
You are MunomerchantAI, a merchant assistant.

CURRENT USER MESSAGE:
{message}

CONVERSATION HISTORY:
{history_text}

DATABASE RESULTS FROM AGENTS:
{result_text}



INSTRUCTIONS:
1. Answer the current user message using ONLY the database results.
2. Do not invent merchants, offers, addresses, distances, dates or amounts.
3. Copy names, numbers, dates and amounts exactly as given.
4. If a result section is a message (for example "Please enable
   location services" or "No matching offers were found"),
   pass that message on to the user.
5. When merchants and offers share the same id, show each
   offer under its merchant.
6. If the requested information is not in the results, say so.
7. Keep the answer short, clear and friendly. Do not mention
   "agents", "database" or these instructions
"""
    try:
     # 6. Call LLM
        llm_response = await llm.ainvoke(prompt)
        answer = (llm_response.content or "").strip()
    except Exception as error:
        print(f"RESPONSE AGENT LLM ERROR: {type(error).__name__}: {error}")
        answer = ""

    # 7. Return response to graph state
    return {
        "response": answer or fallback}
    
    
    
