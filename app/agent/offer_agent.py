from typing import Any
from app.guardrails.inputguardrails import drop_invalid_state

from app.agent.graph.state import Agentstate
from app.tools.search_tool import search_offerdata
from app.utilities.StateMapping import _WEEKDAY_COLUMNS 






def _offer_valid_days(record: dict) -> str | None:
    """Return the days on which the offer is valid."""
    days = []
    for column,day in _WEEKDAY_COLUMNS:
        if record.get(column):
            days.append(day)
    if not days :
        return None 
    return ",".join(days)  


def _to_llm_view(record: dict) -> dict:
    """Project a database record into user-relevant offer fields."""

    return {
        "id": record["LocationID"],
        "name": record["DBAName"] or record["BusinessName"],
        "city": record["City"],
        "state": record["State"],
        "country": record["CountryName"],
        "category": record["MainCategory"],
        "offer": record["Get1CardFreeOffer"],
        "offer_type": record["OfferTypeLabel"],
        "amount": record["Get1CardAmount"],
        "min_purchase": record["Get1MinPurchaseAmount"],
        "offer_details": record["TheScoop"],
        "offer_valid_until": record["OfferEndDate"],
        "keyword": record["Keywords"],
        "description": record["WebsiteDescription"],
        "offer_valid_days": _offer_valid_days(record),
        "offer_redeem_start_time": record.get("RedeemOfferStartTime"),
        "offer_redeem_end_time": record.get("RedeemOfferEndTime"),
    }
    
def _to_float_or_none(value:Any)-> float | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("","null",None):
        return None
    return float(text)

def _to_int(value: Any, default: int = 10) -> int:
    if value is None:
        return default

    text = str(value).strip().lower()

    if text in ("", "null", "none"):
        return default

    return int(float(text))


async def offer_agent(state: Agentstate) -> dict:
    """
    Offer Agent:
    1. Reads offer search parameters from Agentstate.
    2. Validates and normalizes parameters.
    3. Executes the existing SQL service.
    4. Projects database rows into offer results.
    5. Returns results to LangGraph state.
    """
    
    offer_search_params = state.get("offer_search_params", [])
    
    if not offer_search_params:
        return {"offer_results": []}
    all_results = []
    
    for params in offer_search_params:
        name = params.get("name","")
        offer_type = params.get("offer_type", "")
        min_percent = params.get("min_percent", "")
        max_percent = params.get("max_percent", "")
        city = params.get("city", "")
        state_name = params.get("state", "")
        category = params.get("category", "")
        limit = params.get("limit", "10")
        keyword = params.get("keywords", [])
        description = params.get("description", "")
        
        validstate = drop_invalid_state(state_name)
        
        keyword_list = []
        
        for item in (keyword or []):
            if item:
                itemdata = item.strip()
                if itemdata:
                    keyword_list.append(itemdata)
                    
             # Execute the existing database service.
        records = await search_offerdata(
            name=name or None,
            offer_type=offer_type or None,
            min_percent=_to_float_or_none(min_percent),
            max_percent=_to_float_or_none(max_percent),
            city=city or None,
            state=validstate or None,
            category=category or None,
            limit=_to_int(limit),
            keywords=keyword_list or None,
            description=description or None,
            merchant_names=None,
        )
        
        for record in records:
            results = _to_llm_view(record)
            
        all_results.extend(results)
    return {
        "offer_results": all_results
    }