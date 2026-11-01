from typing import Any
from datetime import date, datetime, timedelta

from app.agent.graph.state import Agentstate

from app.tools.redemption_tool import get_redemption_history
from app.utilities.StateMapping import OFFER_TYPE_LABELS
from app.utilities.log import Applogger

logger = Applogger.get_loggerdata(__name__)


# Used when the user types "show my redemption history" without the
# mobile app's date-range picker supplying FromDate/ToDate.
DEFAULT_HISTORY_DAYS = 30

# The deterministic answer lists at most this many rows; the summary
# line (count/total) still covers every row in the range.
MAX_LISTED_REDEMPTIONS = 20

def _to_date(value,field_name):
    """
    Accept a date, a datetime, or an ISO "YYYY-MM-DD" string.
    """
    
    if isinstance(value,datetime):
        return value.date()
    
    if isinstance(value,date):
        return value
    try:
        text = str(value).strip()
        dateoutput = date.fromisoformat(text[:10])
        return dateoutput
    
    except(TypeError,ValueError):
        raise ValueError(
            f"Invalid {field_name}: {value}."
            "Expected a date in YYYY-MM-DD format"
        )
        
def _is_blank(value: Any) -> bool:
    """Small models send None, "", "null" or "none" for no value."""
    return value is None or str(value).strip().lower() in ("", "null", "none") 

def resolve_date_range(from_date=None,to_date=None):
    """
    Normalize an optional from/to pair into two dates.

    - Neither given -> the last DEFAULT_HISTORY_DAYS days up to today.
    - Only one given -> the other defaults (to_date -> today,
      from_date -> DEFAULT_HISTORY_DAYS before to_date).
    """
    def _blank(value):
        if value is None:
            return True
        text = str(value).strip().lower()
        if text in ("","none","null"): 
            return True
        
        return False
       
   
    if _blank(to_date):
        
        todate = date.today()
    else :
        
        todate = _to_date (to_date,"to_date")
        
    if _blank(from_date):
        fromdate = todate - timedelta(days =DEFAULT_HISTORY_DAYS)
            
    else :
        fromdate = _to_date(from_date,"from_date")
        
    if fromdate > todate:
         raise ValueError("from_date cannot be after to_date.")
     
    return fromdate,todate
        
# Only the fields the answer actually needs.
def _to_llm_view(record: dict)->dict:
    redeemed_on = record["RedeemedOn"]
    amount_spent = record["GrossSale"]
     # Format the date
    if redeemed_on:
        formatted_date = redeemed_on.strftime("%Y-%m-%d %I:%M %p")
    else:
        formatted_date = None

    # Format the amount spent
    if amount_spent is not None:
        formatted_amount = f"${amount_spent:.2f}"
    else:
        formatted_amount = None
    return {
        "id": record["LocationID"],
        "name": record["DBAName"] or record["BusinessName"],
        "city": record["City"],
        "state": record["State"],
        "redeemed_on": formatted_date,
        "amount_spent": formatted_amount,
        "offer": _offer_text(record),
        "offer_type": OFFER_TYPE_LABELS.get(record["OfferType"]),
    }

def _offer_text(record):
    
    # Prefer the text-based offer
    free_offer = record["Get1CardFreeOffer"]

    if free_offer:
        return free_offer

    # Otherwise, check the discount amount
    amount = record["Get1CardAmount"]

    if not amount:
        return None

    # Build the offer description
    offer = f"${amount} off"

    # Add the minimum purchase when available
    minimum = record["Get1MinPurchaseAmount"]

    if minimum:
        offer += f" with a minimum purchase of ${minimum}"

    return offer

async def redemptionagent(state: Agentstate) -> dict :
    """
    Get the CURRENT USER's own offer redemption history.

    - customer_id comes from the login token (set in chat.py),
      never from the LLM.
    - Dates: the app's date picker first, then the dates the LLM
      read from the message, otherwise the last 30 days.
    - An empty params object is valid: the search is always limited
      to this customer, so it is never an unfiltered search.
    """
    customer_id = state.get("customer_id")

    if _is_blank(customer_id):
            logger.warning("redemption_search_skipped reason=no_customer_id")
            message = "Please log in to see your redemption history."
            print("REDEMPTION RESULTS:", message)
            return {
            "redemption_results": message,
            "all_agent_results": [
                {
                    "agent": "RedemptionAgent",
                    "results": message
                }
            ]
        }

    # One redemption request per message.
    params_list = state.get("redemption_agent_params") or [{}]
    params = params_list[0]

    from_date = state.get("from_date") or params.get("from_date")
    to_date = state.get("to_date") or params.get("to_date")
    merchant_name = params.get("merchant_name")

    try:
        from_value, to_value = resolve_date_range(from_date,to_date)
        records = await get_redemption_history(customer_id,from_value,to_value,merchant_name)
        
    except ValueError as error:
        logger.info("redemption_search_rejected customer_id=%s error=%s", customer_id, error)
        # Bad dates: show the message to the user
        message = str(error)
        print("REDEMPTION RESULTS:", message)
        return {
            "redemption_results": message,
            "all_agent_results": [
                {
                    "agent": "RedemptionAgent",
                    "results": message
                }
            ]
        }

    except Exception:
        logger.exception("redemption_search_failed customer_id=%s", customer_id)
        # Database or other errors: don't crash the whole graph
        message = "Sorry, I couldn't load your redemption history right now."
        print("REDEMPTION RESULTS:", message)
        return {
            "redemption_results": message,
            "all_agent_results": [
                {
                    "agent": "RedemptionAgent",
                    "results": message
                }
            ]
        }

    period = (
        f"{from_value.isoformat()} to {to_value.isoformat()}"
    )
    if not records:
            logger.info("redemption_search customer_id=%s results=0", customer_id)
            message = f"You have no redemptions from {period}."
            print("REDEMPTION RESULTS:", message)
            return {
            "redemption_results": message,
            "all_agent_results": [
                {
                    "agent": "RedemptionAgent",
                    "results": message
                }
            ]
        }

        
    count = len(records)

    logger.info("redemption_search customer_id=%s results=%d", customer_id, count)
    
    noun = "redemption" if count == 1 else "redemptions"
    
    summary = (
        f"You have {count} {noun} from {period}."
    )
    
    # Only the latest rows are listed below, so say so in the summary
    if count > MAX_LISTED_REDEMPTIONS:
        summary += f" Showing the latest {MAX_LISTED_REDEMPTIONS}."
    # Step 10: Prepare the results for the response agent
    results = [
        {
            "summary": summary
        }
    ]
     # Step 11: Include the latest 20 transaction records
    for record in records[:MAX_LISTED_REDEMPTIONS]:
        results.append(_to_llm_view(record))

    # Step 12: Return the results to the LangGraph workflow
    print("REDEMPTION RESULTS:", results)
    return {
        "redemption_results": results,
        "all_agent_results": [
        {
            "agent": "RedemptionAgent",
            "results": results
        }
    ]
    }


    
    
        
        