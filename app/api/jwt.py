from datetime import datetime, timedelta, timezone


from jose import JWTError, jwt

from uuid import uuid4

import os


# ---------------------------------
# JWT configuration
# ---------------------------------
# Every setting is checked once, when the app starts. A wrong or
# missing value stops startup with a clear message, instead of
# failing later on the first login or chat request.

# Fixed in code, never read from .env: a changed algorithm (e.g.
# "none", or RS256 with the HMAC key) is a known JWT attack.
ALGORITHM = "HS256"

# Minimum key length. The key is shared at project level and is
# shorter than the 32 bytes (256 bits) HS256 ideally uses, so the
# existing 16-byte minimum is kept.
MIN_SIGNING_KEY_BYTES = 16

# Values copied from examples or docs that must never be used.
_PLACEHOLDER_KEYS = {
    "secret", "changeme", "change-me", "your-secret-key",
    "your_secret_key", "mysecret", "supersecret", "jwtsecret",
}

# 1 minute .. 7 days
MIN_EXPIRE_MINUTES = 1
MAX_EXPIRE_MINUTES = 7 * 24 * 60


def get_required_env(name: str) -> str:
    value = os.getenv(name)

    if not value or not value.strip():
        raise RuntimeError(
            f"Missing required environment variable: {name}"
        )

    return value.strip()


def _load_signing_key() -> str:
    key = get_required_env("ISSUER_SIGNING_KEY")

    if key.lower() in _PLACEHOLDER_KEYS:
        raise RuntimeError(
            "ISSUER_SIGNING_KEY is a placeholder value. Generate a real key."
        )

    if len(key.encode("utf-8")) < MIN_SIGNING_KEY_BYTES:
        raise RuntimeError(
            f"ISSUER_SIGNING_KEY must be at least {MIN_SIGNING_KEY_BYTES} "
            f"bytes for {ALGORITHM}"
        )

    return key


def _check_algorithm_setting() -> None:
    """ALGORITHM in .env is optional, but if set it must be HS256."""
    configured = (os.getenv("ALGORITHM") or "").strip().strip('"').strip("'")

    if configured and configured.upper() != ALGORITHM:
        raise RuntimeError(
            f"ALGORITHM={configured} is not supported. Only {ALGORITHM} is allowed."
        )


def _load_expire_minutes() -> int:
    raw = os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440").strip()

    try:
        minutes = int(raw)
    except ValueError:
        raise RuntimeError(
            "ACCESS_TOKEN_EXPIRE_MINUTES must be a whole number of minutes"
        ) from None

    if not MIN_EXPIRE_MINUTES <= minutes <= MAX_EXPIRE_MINUTES:
        raise RuntimeError(
            f"ACCESS_TOKEN_EXPIRE_MINUTES must be between "
            f"{MIN_EXPIRE_MINUTES} and {MAX_EXPIRE_MINUTES}"
        )

    return minutes


VALID_ISSUER = get_required_env("VALID_ISSUER")

VALID_AUDIENCE = get_required_env("VALID_AUDIENCE")

_CLAIM_NAME = get_required_env("_CLAIM_NAME")
_CLAIM_EMAIL = get_required_env("_CLAIM_EMAIL")
_CLAIM_ROLE = get_required_env("_CLAIM_ROLE")

ACCESS_TOKEN_EXPIRE_MINUTES = _load_expire_minutes()

ISSUER_SIGNING_KEY = _load_signing_key()

_check_algorithm_setting()

# A token without these claims is rejected. Without require_exp, a
# token that has no "exp" claim would never expire.
_DECODE_OPTIONS = {
    "require_exp": True,
    "require_iss": True,
    "require_aud": True,
}


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


def decode_access_token(token: str) -> int:

    try:

        payload = jwt.decode(
            token,
            ISSUER_SIGNING_KEY,
            algorithms=[ALGORITHM],
            audience=VALID_AUDIENCE,
            issuer=VALID_ISSUER,
            options=_DECODE_OPTIONS,
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
