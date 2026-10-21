from datetime import datetime,timedelta,timezone


from jose import JWTError,jwt

from uuid import uuid4

# Must match ASP.NET JwtConstants exactly
ISSUER_SIGNING_KEY = "digitalsavingsforsynergyapp"
VALID_ISSUER = "iTabula.SynergyApp.MobileAppService"
VALID_AUDIENCE = "iTabula.SynergyApp.MobileAppService"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours, same as ASP.NET
# ASP.NET ClaimTypes full URIs
_CLAIM_NAME = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name"
_CLAIM_EMAIL = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"
_CLAIM_ROLE = "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"


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
        "WhiteLabelId": str(customer.get("white_label_id") or ""),
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
    
