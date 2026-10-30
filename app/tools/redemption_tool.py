import asyncio
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text

from app.database.database import get_connection
from app.tools.merchant_tool import _clean, _like
from app.tools.offer_search_tool import _NAME_FILTER 

# RedeemedOn is stored in UTC. This database has no timezone lookup
# table: ALL_Get1CardLocations.TimeZoneID is 1-6, and checked against
# each location's State it is the six US zones below. AT TIME ZONE
# takes Windows zone names and handles daylight saving itself.
# The converted value keeps the name RedeemedOn

_REDEMPTION_QUERY = """
                    SELECT 
                    R.LoadRedeemID,
                    R.GrossSale,
                    CAST(
                        R.RedeemedOn AT Time ZONE 'UTC' AT TIME ZONE
                        CASE L.TimeZoneID
                        WHEN 1 THEN 'Eastern Standard Time'
                        WHEN 2 THEN 'Central Standard Time'
                        WHEN 3 THEN 'Mountain Standard Time'
                        WHEN 4 THEN 'Pacific Standard Time'
                        WHEN 5 THEN 'Alaskan Standard Time'
                        WHEN 6 THEN 'Hawaiian Standard Time'
                        ELSE 'UTC'
                    END
                    AS datetime
                ) AS RedeemedOn,

        L.LocationID,
        L.DBAName,
        L.BusinessName,
        L.City,
        L.State,

        O.OfferType,
        O.Get1CardFreeOffer,
        O.Get1CardAmount,
        O.Get1MinPurchaseAmount
        
    FROM [DigitalSavings].[dbo].[ALL_Get1CardLoadRedeem] AS R

    LEFT JOIN [DigitalSavings].[dbo].[ALL_Get1CardLocationOffers] AS LO
        ON LO.LocationOfferID = R.LocationOfferID

    LEFT JOIN [DigitalSavings].[dbo].[ALL_Get1CardLocations] AS L
        ON L.LocationID = LO.LocationID

    LEFT JOIN [DigitalSavings].[dbo].[ALL_Get1CardOffers] AS O
        ON O.OfferID = LO.OfferID
        
    WHERE R.CustomerID = :customer_id
    AND R.Redeemed = 1
    AND R.RedeemedOn >= :start_date
    AND R.RedeemedOn < :end_date
"""

def _get_redemptions_sync(
    customer_id: int,
    start_date: datetime,
    end_date: datetime,
    merchant_name: str | None,
) -> list[dict]:
    query = _REDEMPTION_QUERY
    params = {
        "customer_id" : customer_id,
        "start_date" : start_date,
        "end_date" : end_date,
    }
    if merchant_name:
        query += _NAME_FILTER
        params["name"] = _like(merchant_name)
        
    query += " ORDER BY R.RedeemedOn DESC"
    
    # Step 4: Get the database connection
    connection = get_connection()
    db = None

    try:
        db = next(connection)

        # Step 5: Execute SQL and fetch the records
        rows = db.execute(text(query),params,).mappings().all()
        return [dict(row) for row in rows]
    finally:
        # Step 7: Close database resources
        if db is not None:
            db.close()

        connection.close()
        
async def get_redemption_history(
    customer_id: int | str,
    from_date: date,
    to_date: date,
    merchant_name: str | None = None,
) -> list[dict]:

    # Step 1: Validate the customer ID
    try:
        customer_id = int(customer_id)
    except (TypeError, ValueError):
        raise ValueError("customer_id must be a valid integer.")
    
      # Step 2: Convert the start date to midnight
    start_date = datetime.combine(
        from_date,
        datetime.min.time(),
    )
    # Step 3: Make the end date inclusive
    end_date = datetime.combine(
        to_date + timedelta(days=1),
        datetime.min.time(),
    )
     # Step 4: Clean the optional merchant name
    merchant_name = _clean(merchant_name)

    # Step 5: Call the database function without blocking
    records = await asyncio.to_thread(
        _get_redemptions_sync,
        customer_id,
        start_date,
        end_date,
        merchant_name,
    )
    return records
