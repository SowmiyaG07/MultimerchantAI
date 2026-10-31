import re

from app.agent.graph.state import Agentstate
from app.tools.near_bymerchant_tool import (
    find_merchant_location,
    get_route,
    search_nearbymerchant,
)
from app.utilities.log import Applogger

logger = Applogger.get_loggerdata(__name__)


DEFAULT_RADIUS_KM = 5.0
MAX_RADIUS_KM = 50.0
KM_PER_MILE = 1.609344

# Google Routes API travel modes, plus the words users type.
TRAVEL_MODES = {
    "drive": "DRIVE",
    "driving": "DRIVE",
    "car": "DRIVE",
    "walk": "WALK",
    "walking": "WALK",
    "bike": "BICYCLE",
    "bicycle": "BICYCLE",
    "cycling": "BICYCLE",
}

TRAVEL_MODE_PHRASES = {
    "DRIVE": "by car",
    "WALK": "on foot",
    "BICYCLE": "by bike",
}

# Shown when the app sends no location.
LOCATION_REQUIRED_MESSAGE = (
    "I need your location for this. Please enable location "
    "services for the app and ask again."
)


def _is_blank(value) -> bool:
    """Small models send None, "", "null" or "none" for no value."""
    return value is None or str(value).strip().lower() in ("", "null", "none")


def _to_text(value):
    """A trimmed string, or None when blank ("null" is not a cuisine)."""
    if _is_blank(value):
        return None
    return str(value).strip()


def _to_float(value):
    """A number, or None when blank or not a number."""
    if _is_blank(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value, default=10):
    number = _to_float(value)
    if number is None:
        return default
    return max(1, min(int(number), 50))


def _user_location(state):
    """
    The user's device location as (latitude, longitude), or None
    when location services are off or the values are not valid.

    The mobile app sends null (or sometimes 0, 0) when the user
    has not allowed location access.
    """
    latitude = _to_float(state.get("latitude"))
    longitude = _to_float(state.get("longitude"))

    # Location off: nothing sent, or only one value sent
    if latitude is None or longitude is None:
        return None

    # 0, 0 is a "no location" placeholder, not a real place
    if latitude == 0 and longitude == 0:
        return None

    if latitude < -90 or latitude > 90:
        return None

    if longitude < -180 or longitude > 180:
        return None

    return latitude, longitude


def _radius_km(params) -> float:
    """radius_miles wins over radius_km; default 5 km, max 50 km."""
    miles = _to_float(params.get("radius_miles"))
    km = _to_float(params.get("radius_km"))

    if miles is not None:
        km = miles * KM_PER_MILE

    if km is None or km <= 0:
        return DEFAULT_RADIUS_KM

    return min(km, MAX_RADIUS_KM)


def _merchant_name(record) -> str:
    return record["DBAName"] or record["BusinessName"] or ""


def _normalize_name(value) -> str:
    """ "Joe's KC-BBQ" -> "joeskcbbq" """
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _address(record) -> str:
    parts = [record["Address"], record["City"], record["State"]]
    return ", ".join(str(part).strip() for part in parts if part)


# Only the fields the answer actually needs.
def _to_llm_view(record) -> dict:
    distance_km = float(record["distance_km"])
    return {
        "id": record["LocationID"],
        "name": _merchant_name(record),
        "category": record["CategoryName"],
        "cuisine": record["CuisineName"],
        "address": _address(record),
        "phone": record["ContactPhone"],
        "distance": (
            f"{distance_km / KM_PER_MILE:.1f} miles "
            f"({distance_km:.1f} km) straight line"
        ),
    }


async def _nearby_search(params, latitude, longitude):
    """Type 1: merchants within a radius of the user."""
    radius = _radius_km(params)

    records = await search_nearbymerchant(
        latitude=latitude,
        longitude=longitude,
        radius_km=radius,
        category=_to_text(params.get("category")),
        cuisine=_to_text(params.get("cuisine")),
        keyword=_to_text(params.get("keyword")),
        limit=_to_int(params.get("limit")),
    )

    if not records:
        return (
            f"No matching places were found within "
            f"{radius / KM_PER_MILE:.1f} miles ({radius:.1f} km) of you."
        )

    return [_to_llm_view(record) for record in records]


async def _route_to_merchant(params, latitude, longitude):
    """Type 2: how far is one named merchant from the user."""
    name = _to_text(params.get("merchant_name"))

    mode_text = (_to_text(params.get("travel_mode")) or "").lower()
    travel_mode = TRAVEL_MODES.get(mode_text, "DRIVE")

    records = await find_merchant_location(latitude, longitude, name)

    if not records:
        return f'I couldn\'t find a merchant named "{name}".'

    # "pizza" can match several different merchants. Exact name
    # matches win; otherwise every row must be the same merchant.
    wanted = _normalize_name(name)
    exact = [r for r in records if _normalize_name(_merchant_name(r)) == wanted]

    if exact:
        records = exact
    else:
        names = sorted({_merchant_name(r) for r in records})
        if len(names) > 1:
            return (
                f'More than one merchant matches "{name}": '
                f"{', '.join(names[:5])}. Which one do you mean?"
            )

    # Rows are nearest first, so this is the closest branch.
    merchant = records[0]
    label = f"{_merchant_name(merchant)} ({_address(merchant)})"

    route = await get_route(
        user_latitude=latitude,
        user_longitude=longitude,
        merchant_latitude=float(merchant["Latitude"]),
        merchant_longitude=float(merchant["Longitude"]),
        travel_mode=travel_mode,
    )

    mode_phrase = TRAVEL_MODE_PHRASES[travel_mode]

    if route is None:
        return f"Google Maps couldn't find a route {mode_phrase} to {label}."

    return (
        f"{label} is about {route['distance_miles']} miles "
        f"({route['distance_km']} km) away. "
        f"It takes around {route['duration_minutes']} min {mode_phrase}."
    )


async def nearby_merchanagent(state: Agentstate) -> dict:
    """
    Two kinds of nearby request, both from the user's GPS location:

    1. "Restaurants near me" / "Italian food within 3 miles"
       -> database search inside a radius, nearest first.
    2. "How far is Dreamland BBQ?" (merchant_name is set)
       -> the merchant's location from the database, then the
          road distance and travel time from Google Maps.

    - latitude/longitude come from the request (Agentstate),
      never from the LLM.
    - An empty params object is valid: it means restaurants
      within the default radius.
    """
    # One nearby request per message.
    params_list = state.get("nearby_agent_params") or [{}]
    params = params_list[0]

    # Check the location FIRST: both "restaurants near me" and
    # "how far is X?" need it, so don't search without it.
    location = _user_location(state)

    if location is None:
        logger.info("nearby_location_required")
        return {
        "nearby_merchantresults": LOCATION_REQUIRED_MESSAGE,

        "all_agent_results": [
            {
                "agent": "NearbyAgent",
                "results": LOCATION_REQUIRED_MESSAGE
            }
        ]
    }

    latitude, longitude = location

    try:
        if _is_blank(params.get("merchant_name")):
            results = await _nearby_search(params, latitude, longitude)
        else:
            results = await _route_to_merchant(params, latitude, longitude)

    except RuntimeError as error:
        logger.error("route_lookup_failed error=%s", error)
        # Google Maps key missing or the request failed.
        message = "Sorry, I couldn't calculate the distance right now."
        return {
            "nearby_merchantresults": message,
            "all_agent_results": [
                {
                    "agent": "NearbyAgent",
                    "results": message
                }
            ]
        }

    except Exception:
        logger.exception("nearby_search_failed")
        # Database or other errors: don't crash the whole graph.
        message = "Sorry, I couldn't search nearby places right now."
        return {
            "nearby_merchantresults": message,
            "all_agent_results": [
                {
                    "agent": "NearbyAgent",
                    "results": message
                }
            ]
        }


    # A list is a radius search; a string is a route answer or a message.
    if isinstance(results, list):
        logger.info("nearby_search results=%d", len(results))
    else:
        logger.info("nearby_message")

    return {"nearby_merchantresults": results,
             "all_agent_results": [
        {
            "agent": "NearbyAgent",
            "results": results
        }
    ]}
