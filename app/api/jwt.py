from datetime import datetime,timedelta,timezone


from jose import JWTError,jwt

from uuid import uuid4




def create_access_token(customer:dict) -> str:
    
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    fullname = f"{customer.get('first_name','')} {customer.get('last_name','')}".strip()
    
    payload = {
        "sub" : fullname,
        "jti" : uuid4().hex,
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

        customer_id = payload.get("CustomerId")

        if not customer_id:
            raise ValueError("CustomerId missing from token")

        return int(customer_id)

    except JWTError as exc:
        raise ValueError("Invalid or expired token") from exc
    
