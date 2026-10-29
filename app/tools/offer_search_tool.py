import asyncio
import math
from typing import Any

from sqlalchemy import text

from app.database.database import get_connection
from app.tools.merchant_tool import _clean, _like
from app.utilities.StateMapping import (
    CITY_ALIASES,
    KEYWORD_EXPANSIONS,
    OFFER_TYPE_LABELS,
    OFFER_TYPE_MAP,
)


# Get1CardAmount is a unit-less number shared by two different
# offer shapes: OfferType=2 stores a dollar amount ("$5 off" ->
# '5.00'), OfferType=3 stores a percentage ("10% off" -> '10').
# The two are numerically indistinguishable without OfferType,
# so any percentage-range filter MUST be scoped to OfferType=3,
# or a "5 to 10" query would also match "$5 to $10 off" offers.

# Offer data only. The location join is there because offers are
# linked to merchants through ALL_Get1CardLocationOffers; it gives
# the LocationID (to match merchant_agent's results), the name and
# the city/state filters. Category, brand, country and description
# are merchant details and come from merchant_tool.
_BASE_QUERY = """
    SELECT TOP (:limit)
        L.LocationID,
        L.DBAName,
        L.BusinessName,
        L.City,
        L.State,

        O.OfferID,
        O.Get1CardFreeOffer,
        O.Get1CardAmount,
        O.Get1MinPurchaseAmount,
        O.TheScoop,
        O.OfferType,
        O.OfferStartDate,
        O.OfferEndDate,
        O.ValidMonday,
        O.ValidTuesday,
        O.ValidWednesday,
        O.ValidThursday,
        O.ValidFriday,
        O.ValidSaturday,
        O.ValidSunday,
        O.RedeemOfferStartTime,
        O.RedeemOfferEndTime

    FROM [DigitalSavings].[dbo].[ALL_Get1CardOffers] AS O

    INNER JOIN [DigitalSavings].[dbo].[ALL_Get1CardLocationOffers] AS LO
        ON O.OfferID = LO.OfferID

    INNER JOIN [DigitalSavings].[dbo].[ALL_Get1CardLocations] AS L
        ON LO.LocationID = L.LocationID

    WHERE O.IsLive = 1
    AND L.IsLive = 1
    AND L.DBAName IS NOT NULL
    AND LTRIM(RTRIM(L.DBAName)) <> ''

    -- Only offers running today.
    AND (O.OfferStartDate IS NULL OR O.OfferStartDate <= GETDATE())
    AND (O.OfferEndDate IS NULL OR O.OfferEndDate >= CAST(GETDATE() AS date))
"""


# Same normalized match as merchant_tool, so both tools find the
# same locations for one merchant name.
_NAME_FILTER = """
    AND (
        REPLACE(REPLACE(REPLACE(REPLACE(LOWER(L.DBAName), ' ', ''), '''', ''), '-', ''), '&', '')
            LIKE REPLACE(REPLACE(REPLACE(REPLACE(LOWER(:name), ' ', ''), '''', ''), '-', ''), '&', '')
        OR REPLACE(REPLACE(REPLACE(REPLACE(LOWER(L.BusinessName), ' ', ''), '''', ''), '-', ''), '&', '')
            LIKE REPLACE(REPLACE(REPLACE(REPLACE(LOWER(:name), ' ', ''), '''', ''), '-', ''), '&', '')
    )
"""

def _to_float(value: Any) -> float | None:
    """Parse a percent bound; "", "null" or junk means no bound."""
    value = _clean(value)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None

def _search_offer_sync(
    name: str | None,
    offer_type: str | None,
    min_percent: float | None,
    max_percent: float | None,
    city: str | None,
    state: str | None,
    keyword: str | None,
    limit: int,
) -> list[dict]:
    
    query = _BASE_QUERY
    params = {
    "limit": limit
}
    if name: 
        query += _NAME_FILTER
        params["name"] = _like(name)
    if min_percent is not None or max_percent is not None:
    
        low = max(math.ceil(min_percent) if min_percent is not None else 1,0)

        high = min(math.floor(max_percent) if max_percent is not None else 100,100)

        if low > high:
           return []

        percent_conditions = []

        for value in range(low, high + 1):
           key = f"percent_{value}"

           percent_conditions.append(
            f"(' ' + O.Get1CardFreeOffer) LIKE :{key}"
        )

           params[key] = f"%[^0-9]{value}[%]%"

    # IMPORTANT: outside the for loop
        query += " AND O.OfferType = 3"
        query += " AND (" + " OR ".join(percent_conditions) + ")"

    else:
       offer_type_code = OFFER_TYPE_MAP.get(offer_type.lower()) if offer_type else None

       if offer_type_code is not None:
            query += " AND O.OfferType = :offer_type_code"
            params["offer_type_code"] = offer_type_code

    if city:
        query += " AND L.City LIKE :city"

        params["city"] = _like(
        CITY_ALIASES.get(city.lower(), city)
    )


    if state:
        query += " AND L.State LIKE :state"

        params["state"] = _like(state)
    
    if keyword:
    
        phrase = keyword.lower().removeprefix("free ").strip()

        terms = KEYWORD_EXPANSIONS.get(
            phrase,
            [phrase]
        )

        keyword_conditions = []

        for index, term in enumerate(terms):

            key = f"keyword_{index}"

            keyword_conditions.append(
            f"(O.Get1CardFreeOffer LIKE :{key} "
            f"OR O.TheScoop LIKE :{key})"
        )

            params[key] = _like(term)

        if keyword_conditions:
            query += " AND (" + " OR ".join(keyword_conditions) + ")"
    query += " ORDER BY TRY_CAST(O.Get1CardAmount AS FLOAT) DESC"

    connection = get_connection()

    db = None

    try:
        db = next(connection)

        rows = db.execute(text(query),params).mappings().all()
        
        results = []

        for row in rows:
           item = dict(row)

           item["OfferTypeLabel"] = OFFER_TYPE_LABELS.get(item.get("OfferType"),"unknown")

           results.append(item)

        return results


    finally:
        if db is not None:
            db.close()

        connection.close()
        
    

async def searchoffer_data(
    name: str | None = None,
    offer_type: str | None = None,
    min_percent: Any = None,
    max_percent: Any = None,
    city: str | None = None,
    state: str | None = None,
    keyword: str | None = None,
    limit: Any = 10,
) -> list[dict]:
    
    """Search live merchant offers without blocking the event loop."""
    
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(limit, 50))

    # The DB call is blocking, so run it off the event loop.
    return await asyncio.to_thread(
         _search_offer_sync,
        _clean(name),
        _clean(offer_type),
        _to_float(min_percent),
        _to_float(max_percent),
        _clean(city),
        _clean(state),
        _clean(keyword),
        limit,
    )
