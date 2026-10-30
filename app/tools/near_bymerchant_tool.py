import asyncio
import os

import httpx
from sqlalchemy import text

from app.database.database import get_connection
from app.tools.merchant_tool import _clean, _like, _normalize_category
from app.tools.offer_search_tool import _NAME_FILTER


# Each live location has one geofence region pinned at the store
# (ALL_Get1CardRegions). Its Latitude/Longitude is the merchant's
# position. The user's position comes from the app (:lat, :lng).
#
# CROSS APPLY works out the distance ONCE per row, so SELECT, WHERE
# and ORDER BY can all use D.distance_km without repeating the math.
# Haversine formula, 6371 = Earth's radius in km.
_NEARBY_QUERY = """
    SELECT TOP (:limit)
        L.LocationID,
        L.DBAName,
        L.BusinessName,
        L.Address,
        L.City,
        L.State,
        L.Zip,
        L.ContactPhone,

        C.LCAT_Name AS CategoryName,
        C.Main_Category AS MainCategory,
        CU.Cuisine AS CuisineName,

        R.Latitude,
        R.Longitude,
        D.distance_km

    FROM [DigitalSavings].[dbo].[ALL_Get1CardLocations] AS L

    INNER JOIN [DigitalSavings].[dbo].[ALL_Get1CardRegionLocations] AS RL
        ON RL.LocationID = L.LocationID

    INNER JOIN [DigitalSavings].[dbo].[ALL_Get1CardRegions] AS R
        ON R.RegionID = RL.RegionID

    LEFT JOIN [DigitalSavings].[dbo].[LKP_CATEGORIES] AS C
        ON L.Category = C.LCAT_ID

    LEFT JOIN [DigitalSavings].[dbo].[ALL_Get1CardBrands] AS B
        ON L.BrandID = B.BrandID

    LEFT JOIN [DigitalSavings].[dbo].[LKP_Cuisines] AS CU
        ON L.CuisineID = CU.CuisineID

    CROSS APPLY (
        SELECT 6371.0 * 2 * ASIN(SQRT(
            POWER(SIN(RADIANS(R.Latitude - :lat) / 2), 2)
            + COS(RADIANS(:lat)) * COS(RADIANS(R.Latitude))
            * POWER(SIN(RADIANS(R.Longitude - :lng) / 2), 2)
        )) AS distance_km
    ) AS D

    WHERE L.IsLive = 1
    AND L.DBAName IS NOT NULL
    AND LTRIM(RTRIM(L.DBAName)) <> ''

    -- 0,0 is a placeholder, not a real store location.
    AND R.Latitude IS NOT NULL
    AND R.Longitude IS NOT NULL
    AND NOT (R.Latitude = 0 AND R.Longitude = 0)
"""

# "Restaurant" is stored as "Fine & Dining", "Fine&Dining" or
# "Restaurants", so match all of them.
_RESTAURANT_FILTER = """
    AND (
        C.Main_Category LIKE '%Dining%'
        OR C.Main_Category LIKE '%Restaurant%'
        OR C.LCAT_Name LIKE '%Restaurant%'
    )
"""

# How many name matches to read when finding one merchant.
MERCHANT_LOOKUP_LIMIT = 25


def _run_query(query: str, params: dict) -> list[dict]:
    connection = get_connection()
    db = None
    try:
        db = next(connection)
        rows = db.execute(text(query), params).mappings().all()
        return [dict(row) for row in rows]

    finally:
        if db is not None:
            db.close()
        connection.close()


def _search_nearby_sync(
    latitude: float,
    longitude: float,
    radius_km: float,
    category: str | None,
    cuisine: str | None,
    keyword: str | None,
    limit: int,
) -> list[dict]:
    query = _NEARBY_QUERY
    params = {
        "lat": latitude,
        "lng": longitude,
        "radius_km": radius_km,
        "limit": limit,
    }

    # No category, or "restaurant"/"food" -> restaurants only.
    if category is None or category == "Dining":
        query += _RESTAURANT_FILTER
    else:
        query += " AND (C.LCAT_Name LIKE :category OR C.Main_Category LIKE :category)"
        params["category"] = _like(category)

    if cuisine:
        query += " AND CU.Cuisine LIKE :cuisine"
        params["cuisine"] = _like(cuisine)

    if keyword:
        query += """
            AND (
                L.DBAName LIKE :keyword
                OR B.Keywords LIKE :keyword
                OR CU.Cuisine LIKE :keyword
                OR L.WebsiteDescription LIKE :keyword
            )
        """
        params["keyword"] = _like(keyword)

    query += " AND D.distance_km <= :radius_km"
    query += " ORDER BY D.distance_km"

    return _run_query(query, params)


def _find_merchant_sync(
    latitude: float,
    longitude: float,
    name: str,
) -> list[dict]:
    # Same query, no radius: every branch matching the name,
    # nearest to the user first.
    query = _NEARBY_QUERY + _NAME_FILTER + " ORDER BY D.distance_km"
    params = {
        "lat": latitude,
        "lng": longitude,
        "name": _like(name),
        "limit": MERCHANT_LOOKUP_LIMIT,
    }
    return _run_query(query, params)


async def search_nearbymerchant(
    latitude: float,
    longitude: float,
    radius_km: float = 5.0,
    category: str | None = None,
    cuisine: str | None = None,
    keyword: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """
    Live merchants within radius_km of the user, nearest first.
    latitude/longitude must be the user's device location from
    Agentstate, never values from the LLM.
    """
    # The DB call is blocking, so run it off the event loop.
    return await asyncio.to_thread(
        _search_nearby_sync,
        latitude,
        longitude,
        radius_km,
        _normalize_category(category),
        _clean(cuisine),
        _clean(keyword),
        limit,
    )


async def find_merchant_location(
    latitude: float,
    longitude: float,
    name: str,
) -> list[dict]:
    """
    Every live branch matching name, nearest to the user first.
    The merchant's coordinates always come from the database.
    """
    return await asyncio.to_thread(
        _find_merchant_sync,
        latitude,
        longitude,
        name,
    )


# ---------------------------------------------------------
# GOOGLE ROUTES API (road distance + travel time)
# ---------------------------------------------------------

ROUTES_API_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"

# The Routes API requires a field mask and bills by what is asked.
ROUTES_FIELD_MASK = "routes.distanceMeters,routes.duration"


def _lat_lng(latitude: float, longitude: float) -> dict:
    return {"location": {"latLng": {"latitude": latitude, "longitude": longitude}}}


async def get_route(
    user_latitude: float,
    user_longitude: float,
    merchant_latitude: float,
    merchant_longitude: float,
    travel_mode: str = "DRIVE",
) -> dict | None:
    """
    Road distance and travel time from the user to the merchant.
    Returns None when Google finds no route.
    Raises RuntimeError when the key is missing or the call fails.
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_MAPS_API_KEY is not set.")

    body = {
        "origin": _lat_lng(user_latitude, user_longitude),
        "destination": _lat_lng(merchant_latitude, merchant_longitude),
        "travelMode": travel_mode,
    }
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": ROUTES_FIELD_MASK,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(ROUTES_API_URL, json=body, headers=headers)
            response.raise_for_status()
    except httpx.HTTPError as error:
        raise RuntimeError(f"Google Routes API request failed: {error}") from error

    routes = response.json().get("routes") or []
    if not routes:
        return None

    best = routes[0]
    distance_meters = best.get("distanceMeters", 0)
    # Duration comes back as text like "754s".
    duration_seconds = int(float(str(best.get("duration", "0s")).rstrip("s")))

    return {
        "distance_km": round(distance_meters / 1000, 1),
        "distance_miles": round(distance_meters / 1609.344, 1),
        "duration_minutes": round(duration_seconds / 60),
    }
