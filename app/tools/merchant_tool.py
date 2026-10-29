import asyncio

from app.database.database import get_connection

from sqlalchemy import text
from app.utilities.StateMapping import CATEGORY_ALIASES,COUNTRY_ALIASES



# LKP_COUNTRIES stores full names, so "USA" or "US" from the LLM
# would never match "United States".


def _clean(value: str | None) -> str | None:
    """Strip whitespace; treat empty strings as no filter."""
    if value is None:
        return None
    value = str(value).strip()
    return value or None

def _normalize_category(category: str | None) -> str | None:
    """Map a user's category word to the stored category term."""
    category = _clean(category)
    if category is None:
        return None
    return CATEGORY_ALIASES.get(category.lower(), category)

def _normalize_country(country: str | None) -> str | None:
    """Map a country abbreviation to the stored LKP_COUNTRIES name."""
    country = _clean(country)
    if country is None:
        return None
    return COUNTRY_ALIASES.get(country.lower(), country)

def _like(value: str | None) -> str | None:
    """Wrap a filter value for a LIKE match, or None when not set."""
    if value is None:
        return None
    return f"%{value}%"


def _search_merchant_sync(
    name: str | None,
    category:str|None,
    city: str | None,
    state: str | None,
    country: str | None,
    address: str | None,
    neighborhood: str | None,
    keyword: str | None,
    description: str | None,
    limit: int,
) -> list[dict]:
    connection = get_connection()
    db = None
    try:
        db = next(connection)

        result = db.execute(
            text(
                """
                SELECT TOP (:limit)
                    L.LocationID,
                    L.BrandID,
                    L.DBAName,
                    L.BusinessName,
                    L.Address,
                    L.City,
                    L.State,
                    L.Zip,
                    L.Country,
                    L.ContactPhone,
                    L.Neighborhood,
                    L.WebsiteDescription,
                    L.CuisineID,
                    
                    ---Category
                    L.Category AS CategoryID,
                    C.LCAT_Name AS CategoryName,
                    C.Main_Category AS MainCategory,
                    
                    ---Brand
                    B.BrandName,
                    B.Website AS BrandWebsite,
                    B.Facebook,
                    B.Twitter,
                    B.LinkedIn,
                    B.Pinterest,
                    B.Instagram,
                    B.Yelp,
                    B.YouTube,
                    B.OpenTable,
                    B.Keywords,
                        
                    ---Country and cuisine name
                    
                    CO.LCON_Name AS CountryName,
                    CU.Cuisine AS CuisineName,
                    
                    
                    
                    
                
                    
                    -- Media
                    L.PictureURL1,
                    L.PictureURL2,
                    L.PictureURL3,
                    L.PictureURL4,
                    L.PictureURL5,
                    L.VideoLink1,
                    L.VideoLink2,
                    L.VideoLink3,

                    -- Audit
                    L.CreatedOn,
                    L.LastUpdatedOn,
                    L.IsLive

                    
                    
                FROM [DigitalSavings].[dbo].[ALL_Get1CardLocations] AS L
                
                LEFT JOIN [DigitalSavings].[dbo].[LKP_CATEGORIES] AS C
                ON L.Category = C.LCAT_ID

                LEFT JOIN [DigitalSavings].[dbo].[ALL_Get1CardBrands] AS B
                    ON L.BrandID = B.BrandID

                LEFT JOIN [DigitalSavings].[dbo].[LKP_COUNTRIES] AS CO
                    ON L.Country = CO.LCON_ID

                LEFT JOIN [DigitalSavings].[dbo].[LKP_Cuisines] AS CU
                    ON L.CuisineID = CU.CuisineID
                    
          
                WHERE L.IsLive = 1
                AND L.DBAName IS NOT NULL
                AND LTRIM(RTRIM(L.DBAName)) <> ''

                -- Strip spaces, apostrophes, hyphens and ampersands on
                -- both sides, so "dreamlandbbq" matches "Dreamland BBQ"
                -- and "Joes KC BBQ" matches "Joe's KC BBQ".
                AND (
                    :name IS NULL
                    OR REPLACE(REPLACE(REPLACE(REPLACE(LOWER(L.DBAName), ' ', ''), '''', ''), '-', ''), '&', '')
                            LIKE REPLACE(REPLACE(REPLACE(REPLACE(LOWER(:name), ' ', ''), '''', ''), '-', ''), '&', '')
                    OR REPLACE(REPLACE(REPLACE(REPLACE(LOWER(L.BusinessName), ' ', ''), '''', ''), '-', ''), '&', '')
                            LIKE REPLACE(REPLACE(REPLACE(REPLACE(LOWER(:name), ' ', ''), '''', ''), '-', ''), '&', '')
                )

                AND (:city IS NULL OR L.City LIKE :city)
                AND (:state IS NULL OR L.State LIKE :state)

                AND (
                    :country IS NULL
                    OR CO.LCON_Name LIKE :country
                )

                AND (:address IS NULL OR L.Address LIKE :address)
                AND (:neighborhood IS NULL OR L.Neighborhood LIKE :neighborhood)
                AND (:description IS NULL OR L.WebsiteDescription LIKE :description)

                AND (
                    :keyword IS NULL
                    OR B.Keywords LIKE :keyword
                    OR L.DBAName LIKE :keyword
                    OR L.BusinessName LIKE :keyword
                    OR L.WebsiteDescription LIKE :keyword
                    OR CU.Cuisine LIKE :keyword
                )
                AND (
                    :category IS NULL
                    OR C.LCAT_Name LIKE :category
                    OR C.Main_Category LIKE :category
                )

                ORDER BY L.DBAName
                """
            ),
            {
                "limit": limit,
                "name": _like(name),
                "category": _like(category),
                "city": _like(city),
                "state": _like(state),
                "country": _like(country),
                "address": _like(address),
                "neighborhood": _like(neighborhood),
                "description": _like(description),
                "keyword": _like(keyword),
            },
        )
        rows = result.mappings().all()

        
        return [dict(row) for row in rows]
        

    finally:
        if db is not None:
            db.close()
        connection.close()


async def searchmerchant(
    name: str | None = None,
    category: str | None = None,
    city: str | None = None,
    state: str | None = None,
    country: str | None = None,
    address: str | None = None,
    neighborhood: str | None = None,
    keyword: str | None = None,
    description: str | None = None,
    limit: int = 10,
    merchant_names: list[str] | None = None,
) -> list[dict]:
    """
    Search live merchant locations. Returns an empty list when nothing
    matches. category matches the category name or main category.
    merchant_names is accepted for the agent's call signature but not
    filtered on yet.
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(limit, 50))

    # The DB call is blocking, so run it off the event loop.
    return await asyncio.to_thread(
        _search_merchant_sync,
        _clean(name),
        _normalize_category(category),
        _clean(city),
        _clean(state),
        _normalize_country(country),
        _clean(address),
        _clean(neighborhood),
        _clean(keyword),
        _clean(description),
        limit,
    )
