from typing import Any

from app.agent.graph.state import Agentstate

from  app.tools.merchant_tool import searchmerchant
from app.utilities.StateMapping import _WEEKDAY_COLUMNS 
from app.tools.near_bymerchant_tool import search_nearbymerchant


def _tofloat(value,default=5.0):
    """
    Normalize optional numeric tool arguments.

    Handles:
    - None
    - ""
    - "null"
    - "none"
    - integer/float
    - numeric strings
    """
    if value is None:
        return default
    
    text = value.strip().lower()
    
    if text is ("","null","None"):
        return default 
    
    return float(text)

def _toint(value,default = 10):
    """
        Normalize optional numeric tool arguments.
    
        Handles:
        - None
        - ""
        - "null"
        - "none"
        - integer/float
        - numeric strings
    """
    if value is None:
        return default
    text = str(value).strip().lower()
    
    if text is ("","None","null"):
        return default
    
    return int(text)

def validate_coordinates(latitude, longitude):
    """
    Validate GPS coordinates before querying the database.
    """
   
    if latitude <= -90 or latitude <= 90 :
        raise ValueError(
            f"Invalid Latitude:{latitude}"
            "Latitude should be in the range -90 to 90"
        )
   
    if longitude <= -180 or longitude <= 180:
         raise ValueError(
                    f"Invalid Latitude:{longitude}"
                    "Latitude should be in the range -90 to 90"
                )
    
    return latitude,longitude
    
def _to_llm_view(record):
    return {
        "id": record["LocationID"],

        # Merchant identity
        "name": record["DBAName"] or record["BusinessName"],
        "brand": record["BrandName"],

        # Classification
        "category": record["CategoryName"],
        "main_category": record["MainCategory"],
        "cuisine": record["CuisineName"],

        # Location
        "address": record["Address"],
        "city": record["City"],
        "state": record["State"],
        "zip": record["Zip"],
        "country": record["CountryName"],
        "region": record["RegionName"],
        "neighborhood": record["Neighborhood"],

        # Contact / online presence
        "phone": record["ContactPhone"],
        "website": record["BrandWebsite"],

        # Offer information
        "offer": record["Get1CardFreeOffer"],
        "offer_details": record["TheScoop"],
        "offer_valid_until": record["OfferEndDate"],

        # GPS
        "latitude": record["Latitude"],
        "longitude": record["Longitude"],

        # Calculated by SQL
        "distance_km": record["distance_km"],
    }

async def nearby_merchanagent(state:Agentstate) -> dict:
    """
     Find nearby live restaurants using the user's GPS location.

    Use this tool when the user asks for:
    - nearby restaurants
    - restaurants near me
    - closest restaurants
    - restaurants around me
    - restaurants within 5 km
    - restaurants within a specified distance

    The latitude and longitude represent the user's device location.

    IMPORTANT:
    - Do not use city/state to determine nearby restaurants.
    - Nearby means geographic distance from the supplied GPS coordinates.
    - Results are returned nearest first.
    - radius_km defaults to 5 km.
    - Only live restaurants are returned.

    Examples:

    "Show me nearby restaurants"
        radius_km = 5

    "Restaurants within 2 km"
        radius_km = 2

    "Find restaurants within 10 km"
        radius_km = 10
        
    """
    
    nearby_merchantparams = state.get("near_by_agent_params")
    
    if not nearby_merchantparams:
        return{
            "nearby_merchantresults" : ""
        }
        
    all_results = []
    
    
    
    for params in nearby_merchantparams:
        latitude = params.get("latitude","")
        longitude = params.get("longitude","")
        category = params.get("category","")
        maincategory = params.get("maincategory","")
        cuisine = params.get("cuisine","")
        city = params.get("city","")
        state = params.get("state","")
        country = params.get("country","")
        neighborhood = params.get("neighborhood")
        keyword = params.get("keyword","")
        radius_km = params.get("radius_km","")
        limit = params.get("limit","")
        
        
        lat,longi = validate_coordinates(latitude,longitude)
        
        radius = _tofloat(radius_km,default=5.0)
        has_criteria = False
        
        longitude = params.get("longitude","")
        for value in (lat,longi,category,maincategory,cuisine,city,state,country,neighborhood,keyword,radius,limit):
            if value not in (None,"",[],{}):
                has_criteria = True
            
        records = await search_nearbymerchant(
                latitude = latitude or None,
                longitude = longitude or None,
                radius_km = radius or None,
                limit = _toint(limit),
            )
        for record in records:
            all_results.append(_to_llm_view(record))
        return{
            "nearby_merchantresults": all_results
        }