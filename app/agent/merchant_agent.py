
from typing import Any

from app.agent.graph.state import Agentstate

from  app.tools.merchant_tool import searchmerchant
from app.utilities.StateMapping import _WEEKDAY_COLUMNS 
from app.guardrails.inputguardrails import drop_invalid_state,drop_state_derived_from_address


def _to_int(value, default=10):
    """
    Small models emit optional numeric tool args inconsistently -- a
    real number, JSON null (-> None), the *string* "null"/"none", or
    an empty string. Mirrors offers_tools.py's identically-named
    helper so both tools normalize a `limit` argument the same way.
    """
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ("", "null", "none"):
        return default
    return int(float(text))


def _social_media_links(record:str) -> str | None:
    """Combine all available merchant social media links."""
    
    columns = (
        "Facebook",
        "Twitter",
        "LinkedIn",
        "Pinterest",
        "Instagram",
        "Yelp",
        "YouTube",
        "OpenTable",
    )
    links = []
    for column in columns:
        link = record.get(column)
        if link:
            links.append(link)
        if not links:
            return None
        return ",".join(links)
    
    
def _to_llm_view(record: dict) -> dict:
    """Convert a database record into merchant search results."""

    return {
        "id": record["LocationID"],
        "name": record["DBAName"] or record["BusinessName"],
        "brand": record["BrandName"],
        "category": record["CategoryName"],
        "main_category": record["MainCategory"],
        "cuisine": record["CuisineName"],
        "address": record["Address"],
        "city": record["City"],
        "state": record["State"],
        "zip": record["Zip"],
        "country": record["CountryName"],
        "region": record["RegionName"],
        "neighborhood": record["Neighborhood"],
        "phone": record["ContactPhone"],
        "website": record["BrandWebsite"],
        "facebook": record["Facebook"],
        "twitter": record["Twitter"],
        "linkedin": record["LinkedIn"],
        "pinterest": record["Pinterest"],
        "instagram": record["Instagram"],
        "yelp": record["Yelp"],
        "youtube": record["YouTube"],
        "opentable": record["OpenTable"],
        "social_media": _social_media_links(record),
        "description": record["WebsiteDescription"],
        "offer": record["Get1CardFreeOffer"],
        "offer_details": record["TheScoop"],
        "offer_valid_until": record["OfferEndDate"],
        "offer_valid_days": _offer_valid_days(record),
        "offer_redeem_start_time": record.get(
            "RedeemOfferStartTime"
        ),
        "offer_redeem_end_time": record.get(
            "RedeemOfferEndTime"
        ),
        "latitude": record["Latitude"],
        "longitude": record["Longitude"],
        "radius": record["Radius"],
    }
    

def _offer_valid_days(record: dict) -> str | None:
    """Return the days on which the offer is valid."""
    days = []
    for column,day in _WEEKDAY_COLUMNS:
        if record.get(column):
            days.append(day)
    if not days :
        return None 
    return ",".join(days)  

async def MerchantAgent(state: Agentstate) -> dict :
    """
    Instructions:

Does not call an LLM.

Receives merchant search criteria from the Supervisor through Agentstate.

Calls the existing search_merchants tool.

Returns merchant results to LangGraph state in merchant_results.

Does not perform query rewriting or expansion.

Does not invent search criteria or perform an unfiltered search.

Reuses existing tools and follows the project's coding conventions.

Few-shot examples:

Example 1:
User query: "Where is Dreamland BBQ?"
Supervisor criteria: {"name": "Dreamland BBQ"}
Action: Call search_merchants with the merchant name.
Output: Return the actual search results in merchant_results.

Example 2:
User query: "Find restaurants in Dallas."
Supervisor criteria: {"city": "Dallas", "category": "Restaurants"}
Action: Call search_merchants with the provided criteria.
Output: Return the actual search results in merchant_results.

Example 3:
User query: "Find a merchant."
Supervisor criteria: {}
Action: Do not perform an unfiltered search.
Output: Return a message indicating that more search details are required.

"""
    
    merchant_search_params = state.get("merchant_search_params",[])
    
    if not merchant_search_params: 
        return{
            "merchat_results": ""
        }

    all_results = []
    
    for params in merchant_search_params:
        name = params.get("name", "")
        category = params.get("category", "")
        city = params.get("city", "")
        state_name = params.get("state", "")
        country = params.get("country", "")
        address = params.get("address", "")
        neighborhood = params.get("neighborhood", "")
        description = params.get("description", "")
        latitude = params.get("latitude", "")
        longitude = params.get("longitude", "")
        keyword = params.get("keyword", "")
        limit = params.get("limit", "10")
        
        # Validate the state filter.
        valid_state = drop_invalid_state(state_name)
        
        valid_state = drop_state_derived_from_address(valid_state,address)
        
        has_criteria = False
        
        for value in (  name,category,city,valid_state,country,address,neighborhood,description,latitude,longitude,keyword,):
            if value not in (None,"",[],{}):
                has_criteria = True
                
            
            records = await searchmerchant(
            name=name or None,
            category=category or None,
            city=city or None,
            state=valid_state or None,
            country=country or None,
            address=address or None,
            neighborhood=neighborhood or None,
            keyword=keyword or None,
            description=description or None,
            latitude=latitude or None,
            longitude=longitude or None,
            limit=_to_int(limit),
            merchant_names=None,
        )
                # Convert each database row into a user-relevant record.
        for record in records:
            all_results.append(_to_llm_view(record))
        return {
        "merchant_results": all_results
    }


        
        
        


    
