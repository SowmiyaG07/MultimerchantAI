import operator
from typing import Any,Annotated
from typing_extensions import TypedDict
from datetime import date


class Agentstate(TypedDict,total= False): 
    
    #User Input
    message : str 
    session_id : str 
    authenticated_customer_id: int
    
    latitude : float 
    longitude : float 
    from_date : date | None 
    to_date : date | None 
    
    #Query Expandstandin
    intent : str
    expanded_query : str
    expanded_queries : list[str]

    # Supervisor routing
    selected_agent : list[str]
    business_related : bool
    other_customer_requested : bool
    needs_history : bool
    needs_clarification : bool
    chat_history : list[dict]
    customer_id : int
    
    
    merchant_search_params : list[dict[str, Any]]
    offer_search_params : list[dict[str, Any]]
    nearby_agent_params: list[dict[str, Any]]
    redemption_agent_params: list[dict[str, Any]]
    merchant_name : str
    #Individual Agent results
    # Either a message string or a list of records from the tool.
    merchant_results : str | list[dict[str, Any]]
    offer_results : str | list[dict[str, Any]]
    nearby_merchantresults : str | list[dict[str, Any]]
    redemption_results : str | list[dict[str, Any]]
    
    
    #Common Results -Reducer
    all_agent_results: Annotated[list[dict[str, Any]], operator.add]
    
    #Final response
    response_results: str
    response : str
    error_message : str | None
    # Failures in this request. operator.add because parallel agents
    # can fail in the same step; a plain key would raise InvalidUpdateError.
    errors : Annotated[list[str], operator.add]
    