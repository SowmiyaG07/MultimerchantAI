from langgraph.graph import StateGraph, START, END

from app.agent.graph.state import Agentstate

from app.agent.munomultiagent import SupervisorAgent
from app.agent.merchant_agent import MerchantAgent
from app.agent.offer_agent import offer_agent
from app.agent.nearby_agent import nearby_merchanagent
from app.agent.redemption_agent import redemptionagent
from app.agent.response_agent import ResponseAgent



def route_to_agent(state: Agentstate):
    selected_agent = state.get("selected_agent") or []

    # Support one agent or multiple agents
    if isinstance(selected_agent, str):
        return [selected_agent]

    # Not business-related or needs clarification: the supervisor
    # has already set the response, so finish the graph.
    if not selected_agent:
        return END

    return selected_agent


builder = StateGraph(Agentstate)
builder.add_node("supervisor",SupervisorAgent)
builder.add_node("merchant_agent",MerchantAgent)
builder.add_node("offer_agent",offer_agent)
builder.add_node("nearby_agent",nearby_merchanagent)
builder.add_node("redemption_agent",redemptionagent)
builder.add_node("response_agent",ResponseAgent)


builder.add_edge(START,"supervisor")

builder.add_conditional_edges("supervisor",route_to_agent,{
    "merchant_agent": "merchant_agent",
    "offer_agent": "offer_agent",
    "nearby_agent": "nearby_agent",
    "redemption_agent": "redemption_agent",
    END: END,
},)

builder.add_edge("merchant_agent","response_agent")
builder.add_edge("offer_agent","response_agent")
builder.add_edge("nearby_agent","response_agent")
builder.add_edge("redemption_agent","response_agent")


builder.add_edge("response_agent",END)

multiagent = builder.compile()
    