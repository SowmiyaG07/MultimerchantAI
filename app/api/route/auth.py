import asyncio
import base64
import hashlib
import hmac
import os

from Crypto.Cipher import DES3
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, status

from app.api.jwt import create_access_token
from app.api.schema.loginrequest import Loginrequest, Loginresponse
from app.services.customer_service import getcustomerid_email
from app.utilities.log import Applogger

load_dotenv()

authrouter = APIRouter(tags=["Authentication"])

logger = Applogger.get_loggerdata(__name__)


_CRYPTO_SECRET = os.getenv("_CRYPTO_SECRET")

if not _CRYPTO_SECRET:
    # Without it no password can be checked, so every login would fail
    # with "Invalid email or password". Say why in the log.
    logger.error("login_config_missing name=_CRYPTO_SECRET")


# ---------------------------------------------
# Responses
# ---------------------------------------------
# Unknown email and wrong password give the SAME answer, so the login
# endpoint cannot be used to find out which emails have accounts.
# Account status (inactive, misconfigured) is only checked AFTER the
# password is correct, and is shown with one neutral message. The
# real reason is written to the log for support.

INVALID_CREDENTIALS = {
    "code": "INVALID_CREDENTIALS",
    "message": "Invalid email or password",
}

ACCOUNT_UNAVAILABLE = {
    "code": "ACCOUNT_UNAVAILABLE",
    "message": "Unable to sign in with this account. Please contact support.",
}

# Compared against when the email is unknown, so that path does the
# same password work (one encryption + compare) as a real check.
_DUMMY_STORED_PASSWORD = "AAAAAAAAAAAAAAAAAAAAAA=="


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=INVALID_CREDENTIALS,
    )


def _account_unavailable(status_code: int) -> HTTPException:
    return HTTPException(status_code=status_code, detail=ACCOUNT_UNAVAILABLE)


@authrouter.post("/login", response_model=Loginresponse, status_code=200)
async def loginrequest(request: Loginrequest):
    customer = await asyncio.to_thread(getcustomerid_email, request.email)

    # 1. Unknown email: same work and same answer as a wrong password.
    if customer is None:
        _verify_password(request.password, _DUMMY_STORED_PASSWORD)
        logger.warning("login_failed reason=unknown_email")
        raise _invalid_credentials()

    # 2. Password FIRST: nothing about the account is revealed to
    #    someone who does not know the password.
    if not _verify_password(request.password, customer["password"]):
        logger.warning(
            "login_failed reason=wrong_password customer_id=%s",
            customer["customer_id"],
        )
        raise _invalid_credentials()

    # 3. Correct password: now the account status can be checked.
    if not customer["active"]:
        logger.warning(
            "login_failed reason=inactive customer_id=%s",
            customer["customer_id"],
        )
        raise _account_unavailable(status.HTTP_403_FORBIDDEN)

    if not customer["external_id"]:
        logger.error(
            "login_failed reason=missing_external_id customer_id=%s",
            customer["customer_id"],
        )
        raise _account_unavailable(status.HTTP_500_INTERNAL_SERVER_ERROR)

    accesstoken = create_access_token(customer)

    logger.info("login_success customer_id=%s", customer["customer_id"])

    session_id = customer["external_id"]

    return Loginresponse(
        access_token=accesstoken,
        token_type="bearer",
        customer_id=customer["customer_id"],
        first_name=customer["first_name"],
        last_name=customer["last_name"],
        session_id=session_id,
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


def _verify_password(provided: str, stored: str | None) -> bool:
    """Encrypt the provided password and compare it with the stored one."""
    if not provided or not stored:
        return False
    try:
        encrypted = _encrypt_password(provided)
    except Exception:
        logger.exception("password_check_failed")
        return False

    # Constant-time compare: the time taken does not show how many
    # characters matched.
    return hmac.compare_digest(encrypted.encode("utf-8"), stored.encode("utf-8"))
