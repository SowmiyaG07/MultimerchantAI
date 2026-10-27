from typing import Any
from datetime import date, datetime, timedelta

from app.agent.graph.state import Agentstate

from  app.tools.merchant_tool import searchmerchant
from app.utilities.StateMapping import _WEEKDAY_COLUMNS 
from app.tools.near_bymerchant_tool import search_nearbymerchant


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
        return value.date
    
    if isinstance(value,date):
        return value
    try:
        text = str(value).strip()
        dateoutput = text[::10]
        return dateoutput
    
    except(TypeError,ValueError):
        raise ValueError(
            f"Invalid {field_name}:{value}"
            "Expected a date in YYYY-MM-DD format"
        )
        
    

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
        text = value.strip().lower()
        if text in ("","None","null"): 
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
def _to_llm_view(record):
    return {
        "location_id": record["LocationID"],
        "name": record["DBAName"],
        "redeemed_on": record["CreatedOn"],
        "amount": record["Amount"],
        "offer": record["OfferInfo"],
    }
    
async def redemptionagent(state: Agentstate) -> dict :
    customer_id = state.get("customer_id") | None 
    from_date = state.get("from_date") | None 
    to_date = state.get("to_date") | None 
    params_list = state.get("redemption_search_params", [])
    """
    Get the CURRENT USER's own offer redemption history.

    Use this tool when the user asks about their own redemptions:
    - "Show my redemption history"
    - "What offers have I redeemed?"
    - "My redemptions last month"
    - "Where did I use my card?"

    IMPORTANT:
    - customer_id is always supplied by the application from the
      logged-in user -- pass "" for it; any value you pass is ignored.
    - from_date / to_date are dates in YYYY-MM-DD format. Only set
      them when the user states a period; otherwise leave them ""
      and the last 30 days are used.
    - This is NOT a merchant or offer search -- never use
      search_merchants or search_offers for redemption history.
    """
    customer_idd =  str(customer_id).strip()
    
    if customer_id is (None or ""):
        raise ValueError("Customer_id is required")
    
    from_value, to_value = resolve_date_range(from_date,to_date)
    records = get_redemption_history(customer_idd,from_value,to_value)
    llm_records =[]
    
    for record in records:
        data = _to_llm_view(record)
        
        llm_records.append(data)
        
    return llm_records

    
    
        
        