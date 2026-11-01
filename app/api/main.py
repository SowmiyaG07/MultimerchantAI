from fastapi import FastAPI, APIRouter,Request

from app.api.route.auth import authrouter
from app.api.route.chat import chatrouter
from app.api.route.health import healthrouter
import time
from app.utilities.log import Applogger
from pydantic import BaseModel
from app.database.database import get_connection
from sqlalchemy import text

Applogger.setup()
logger = Applogger.get_loggerdata(__name__)

app = FastAPI(
    title="MultiAgentMerchant",
    description="AI-powered merchant and offer discovery API",
    version="1.0.0"

)

@app.middleware("http")
async def log_request(request: Request,call_next):
    """Write one log line for every API request."""
    start = time.perf_counter()
    
    try: 
        response = await call_next(request)
    except Exception:
        duration_ms = (time.perf_counter() - start ) * 1000
        logger.exception(
            "api_request_failed method=%s path = %s duration_ms=%.0f",
            request.method,request.url.path,duration_ms,
            
        )
        raise
    duration_ms = (time.perf_counter() - start) *1000
    logger.info("api_request method=%s path=%s status=%s duration_ms=%.0f",
        request.method, request.url.path, response.status_code, duration_ms,
    )
    return response
    



app.router.include_router(
    authrouter, prefix="/api/v1/auth", tags=["Authentication"])
app.router.include_router(chatrouter, prefix="/api/v1", tags=["chat"])
app.router.include_router(
    healthrouter, prefix="/api/v1/health", tags=["health"])



