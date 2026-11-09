from datetime import datetime, timedelta, timezone


from jose import JWTError, jwt

from uuid import uuid4

import os


# ---------------------------------
# JWT configuration
# ---------------------------------

def get_required_env(name: str) -> str:
    value = os.getenv(name)

    if not value or not value.strip():
        raise RuntimeError(
            f"Missing required environment variable: {name}"
        )

    return value.strip()

VALID_ISSUER = get_required_env("VALID_ISSUER")

VALID_AUDIENCE = get_required_env("VALID_AUDIENCE")

_CLAIM_NAME = get_required_env("_CLAIM_NAME")
_CLAIM_EMAIL = get_required_env("_CLAIM_EMAIL")
_CLAIM_ROLE = get_required_env("_CLAIM_ROLE")
ACCESS_TOKEN_EXPIRE_MINUTES = int(
    os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

ISSUER_SIGNING_KEY = os.getenv("ISSUER_SIGNING_KEY")
ALGORITHM = os.getenv("ALGORITHM")

if len(ISSUER_SIGNING_KEY.encode("utf-8")) < 16:
    raise RuntimeError(
        "ISSUER_SIGNING_KEY must be at least 16 bytes for HS256"
    )


def create_access_token(customer: dict) -> str:

    expire = datetime.now(timezone.utc) + \
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    fullname = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip(
    )

    payload = {
        "sub": fullname,
        "jti": uuid4().hex,
        "iss": VALID_ISSUER,
        "aud": VALID_AUDIENCE,
        "exp": expire,
        "CustomerId": str(customer["customer_id"]),
        "WhiteLabelId": str(customer.get("whitelabel_id") or ""),
        _CLAIM_NAME: fullname,
        _CLAIM_EMAIL: customer.get("email", ""),
        _CLAIM_ROLE: "Customer",
    }
    return jwt.encode(payload, ISSUER_SIGNING_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> str:
    
    try:
    
        payload = jwt.decode(
            token,
            ISSUER_SIGNING_KEY,
            algorithms=[ALGORITHM],
            audience=VALID_AUDIENCE,
            issuer=VALID_ISSUER,
        )
    except JWTError as exc:
        raise ValueError("Invalid or expired token") from exc

        
    try:
        customer_id = int(payload.get("CustomerId"))
        if not customer_id:
            raise ValueError("CustomerId missing from token")
        if customer_id <= 0:
            raise ValueError("Invalid CustomerId in token")

        return int(customer_id)
    
    except (TypeError, ValueError):
        raise ValueError(
            "Invalid or missing CustomerId in token"
        )

   
