from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.jwt import decode_access_token
from app.utilities.log import Applogger
from app.cache.redis_cache import check_rate_limit
import os


security = HTTPBearer()

logger = Applogger.get_loggerdata(__name__)

CHAT_RATE_LIMIT = int(os.getenv("CHAT_RATE_LIMIT", "100"))
CHAT_RATE_WINDOW = int(os.getenv("CHAT_RATE_WINDOW", "60"))

def get_current_customer_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:

    token = credentials.credentials

    try:
        return decode_access_token(token)

    except ValueError:

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "INVALID_TOKEN",
                "message": "Invalid or expired authentication token.",
            },
        )
        
async def check_chat_rate_limit(
    customer_id: int = Depends(get_current_customer_id),
) -> int:
    """Allow CHAT_RATE_LIMIT chat requests per customer per CHAT_RATE_WINDOW seconds."""

    retry_after = await check_rate_limit(
        key=f"chat_rate:{customer_id}",
        limit=CHAT_RATE_LIMIT,
        window_seconds=CHAT_RATE_WINDOW,
    )

    if retry_after:
        logger.warning(
            "chat_rate_limited customer_id=%s retry_after=%s",
            customer_id, retry_after,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "RATE_LIMITED",
                "message": "Too many messages. Please try again shortly.",
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )

    return customer_id