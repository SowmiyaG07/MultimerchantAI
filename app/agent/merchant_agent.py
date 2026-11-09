
from app.agent.graph.state import Agentstate

from  app.tools.merchant_tool import searchmerchant
from app.guardrails.inputguardrails import drop_invalid_state,drop_state_derived_from_address
from app.utilities.log import Applogger

logger = Applogger.get_loggerdata(__name__)


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
    try:
        return int(float(text))
    except ValueError:
        return default


def _social_media_links(record: dict) -> str | None:
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
        "neighborhood": record["Neighborhood"],
        "phone": record["ContactPhone"],
        "website": record["BrandWebsite"],
          "social_media": _social_media_links(record),
        "description": record["WebsiteDescription"],
    }

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
            "merchant_results": ""
        }

    all_results = []
    searched = False

    for params in merchant_search_params:
        name = params.get("name", "")
        category = params.get("category", "")
        city = params.get("city", "")
        state_name = params.get("state", "")
        country = params.get("country", "")
        address = params.get("address", "")
        neighborhood = params.get("neighborhood", "")
        description = params.get("description", "")
        keyword = params.get("keyword", "")
        limit = params.get("limit", "10")
        
        # Validate the state filter.
        valid_state = drop_invalid_state(state_name)
        
        valid_state = drop_state_derived_from_address(valid_state,address)
        
        # Only count filters the tool actually applies, so an empty
        # params object never turns into an unfiltered search.
        has_criteria = False
        for value in (name,category,city,valid_state,country,address,neighborhood,description,keyword,):
            if value not in (None,"",[],{}):
                has_criteria = True

        if not has_criteria:
            continue

        searched = True
        try:
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
                limit=_to_int(limit),
                merchant_names=None,
            )
        except Exception:
            # Database or other errors: don't crash the whole graph.
            logger.exception("merchant_search_failed")
            message = "Sorry, I couldn't search merchants right now."
            return {
                "merchant_results": message,
                "all_agent_results": [
                    {
                        "agent": "MerchantAgent",
                        "results": message
                    }
                ]
            }

        # Convert each database row into a user-relevant record.
        for record in records:
            all_results.append(_to_llm_view(record))

    if not searched:
        logger.info("merchant_search_skipped reason=no_criteria")
        message = "Please provide more details, such as a merchant name or city."
        return {
            "merchant_results": message,
            "all_agent_results": [
                {
                    "agent": "MerchantAgent",
                    "results": message
                }
            ]
        }

    if not all_results:
        logger.info("merchant_search results=0")
        message = "No matching merchants were found."
        return {
            "merchant_results": message,
            "all_agent_results": [
                {
                    "agent": "MerchantAgent",
                    "results": message
                }
            ]
        }


    logger.info("merchant_search results=%d", len(all_results))

    return {
        "merchant_results": all_results,
        "all_agent_results": [
        {
            "agent": "MerchantAgent",
            "results": all_results
        }
    ]
    }


        
        
        


    
