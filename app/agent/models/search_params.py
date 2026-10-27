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
    latitude: str = ""
    longitude: str = ""
    keyword: str = ""
    limit: str = "10"


class OfferSearchParams(BaseModel):
    name: str = ""
    keyword: str = ""
    category: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    offer_type: str = ""
    limit: str = "10"


class NearbySearchParams(BaseModel):
    latitude: str = ""
    longitude: str = ""
    category: str = ""
    maincategory: str = ""
    cuisine: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    neighborhood: str = ""
    keyword: str = ""
    radius_km: float | None = None
    limit: str = "10"


class RedemptionSearchParams(BaseModel):
    customer_id: str = ""
    from_date: str = ""
    to_date: str = ""
    merchant_name: str = ""
    limit: str = "10"
