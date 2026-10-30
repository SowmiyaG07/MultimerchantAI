import os
from pydantic import Field, BaseModel
from typing import Any


class MearchantSearchparams(BaseModel):
    name: str = ""
    category: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    address: str = ""
    neighborhood: str = ""
    description: str = ""
    # latitude/longitude are not here on purpose. The merchant tool
    # does not filter on them, and the LLM must not invent coordinates.
    keyword: str = ""
    # int | str: the LLM may return 10 or "10". A plain str field
    # makes Pydantic v2 reject the number.
    limit: int | str = "10"


class OfferSearchParams(BaseModel):
    name: str = ""
    min_percent: float | str | None = None
    max_percent: float | str | None = None
    keyword: str = ""
    # No category/country: those are merchant details, which the
    # merchant agent returns. The offer tool does not filter on them.
    city: str = ""
    state: str = ""
    offer_type: str = ""
    limit: int | str = "10"


class NearbySearchParams(BaseModel):
    # latitude/longitude are not here on purpose. The nearby agent
    # reads the user's device location from Agentstate, so the LLM
    # can never invent or change the coordinates.
    category: str = ""
    cuisine: str = ""
    keyword: str = ""
    radius_km: float | str | None = None
    radius_miles: float | str | None = None
    # Set only for "how far is <merchant>?" questions.
    merchant_name: str = ""
    travel_mode: str = ""
    limit: int | str = "10"


class RedemptionSearchParams(BaseModel):
    # customer_id is not here on purpose. The redemption agent reads it
    # from Agentstate (set from the login token in chat.py), so the LLM
    # can never choose whose redemptions are fetched.
    from_date: str = ""
    to_date: str = ""
    merchant_name: str = ""
    limit: int | str = "10"
