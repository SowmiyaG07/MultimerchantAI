import base64
import hashlib
from datetime import datetime

from Crypto.Cipher import DES3

from fastapi import APIRouter, HTTPException, status

from app.api.schema.loginrequest import Loginresponse,Loginrequest

from app.services.customer_service import getcustomerid_email

from app.database.database import get_connection

from app.api.jwt import create_access_token
from logging import Applogger
_CRYPTO_SECRET = "ChiroTouchCodeNamedCairo"

authrouter = APIRouter(tags=["Authentication"])

logger = Applogger.get_loggerdata(__name__)


@authrouter.post("/login",response_model=Loginresponse,status_code=200)
async def loginrequest(request:Loginrequest):
    customer = getcustomerid_email(request.email)
    
    if customer is None:
        logger.warning("login_failed reason=unknown_email")
        raise HTTPException(
            
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail = {
                "code": "INVALID_CREDENTIALS",
                "message" : "Invalid email or password"
            },
            
        )
        
    if not customer["active"]:
        logger.warning("login_failed reason=inactive customer_id=%s", customer["customer_id"])
        raise HTTPException(
            status_code= status.HTTP_403_FORBIDDEN,
            detail = {
                "code": "CUSTOMERINACTIVE",
                "message" : "customer is not active"
                
            }
            
        )
        
    if not _verify_password(request.password, customer["password"]):
        logger.warning("login_failed reason=wrong_password customer_id=%s", customer["customer_id"])
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail = {
                            "code": "INVALID_CREDENTIALS",
                            "message" : "Invalid password"
                    },
            
        )
        
    if not customer["external_id"]:
        logger.error("login_failed reason=missing_external_id customer_id=%s", customer["customer_id"])
        raise HTTPException(
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail = {
                
                            "code": "MISSING_EXTERNAL_ID",
                            "message" : "CUSTOMER HAS NO eXTERNAL iD CONFIGURED",
                
            }
        )
    
    accesstoken = create_access_token(customer)

    logger.info("login_success customer_id=%s", customer["customer_id"])
    
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
   
    # Also support plain-text password being sent (encrypt and compare)
    
    if not provided or not stored:
        return False
    try:
       
        return _encrypt_password(provided) == stored
    except Exception:
        return False
        
            


