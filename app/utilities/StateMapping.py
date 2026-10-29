STATE_MAPPING = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
    "district of columbia": "DC",
}


def normalize_state(state: str) -> str:
    if not state:
        return ""

    state = state.strip().lower()

    return STATE_MAPPING.get(state, state.upper())



_WEEKDAY_COLUMNS = (
    ("ValidMonday", "Monday"),
    ("ValidTuesday", "Tuesday"),
    ("ValidWednesday", "Wednesday"),
    ("ValidThursday", "Thursday"),
    ("ValidFriday", "Friday"),
    ("ValidSaturday", "Saturday"),
    ("ValidSunday", "Sunday"),
)

CATEGORY_ALIASES = {
    "restaurant": "Dining",
    "restaurants": "Dining",
    "food": "Dining",
    "dining": "Dining",
    "eat": "Dining",
    "cafe": "Cafe",
    "cafes": "Cafe",
    "coffee": "Cafe",
    "car": "Automotive",
    "auto": "Automotive",
    "gym": "Fitness",
    "gyms": "Fitness",
}

COUNTRY_ALIASES = {
    "usa": "United States",
    "us": "United States",
    "u.s.": "United States",
    "u.s.a.": "United States",
    "america": "United States",
    "united states of america": "United States",
}

OFFER_TYPE_MAP = {
    "free": 1,
    "freeitem": 1,
    "complimentary": 1,
    "amount": 2,
    "dollar": 2,
    "amount_off": 2,
    "percentage": 3,
    "percent": 3,
    "percentage_off": 3,
}

OFFER_TYPE_LABELS = {
    1: "free_item",
    2: "amount_off",
    3: "percentage_off",
}

CITY_ALIASES = {
    "sandiego": "San Diego",
    "san diego": "San Diego",
    "sanfrancisco": "San Francisco",
    "san francisco": "San Francisco",
    "newyork": "New York",
    "new york": "New York",
    "losangeles": "Los Angeles",
    "los angeles": "Los Angeles",
}

# A keyword like "dessert" is a category the user asks for, not a
# literal word that appears in the DB -- the actual offer text says
# "banana pudding", never "dessert". Without this expansion, a
# correctly name-matched merchant's offer gets excluded by the
# keyword AND-filter and looks like a false "not found". Mirrors
# the same table in merchant_service.py's search_merchantdata.
KEYWORD_EXPANSIONS = {
    "dessert": [
        "dessert",
        "banana pudding",
        "cake",
        "ice cream",
        "pie",
        "brownie",
        "cookie",
    ],
    "bbq": [
        "bbq",
        "barbecue",
    ],
}
