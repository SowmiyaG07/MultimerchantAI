from fastapi import FastAPI, APIRouter,Request

from app.api.route.auth import authrouter
from app.api.route.chat import chatrouter
from app.api.route.health import healthrouter
import os
import time
from dotenv import load_dotenv
from app.utilities.log import Applogger
from pydantic import BaseModel
from app.database.database import get_connection
from sqlalchemy import text
from app.utilities.log import Applogger
from app.utilities.server_settings import WORKER_COUNT
from app.agent.llm_limits import LLM_MAX_CONCURRENT_CALLS, LLM_EFFECTIVE_TOTAL_CALLS


load_dotenv()

Applogger.setup()
logger = Applogger.get_loggerdata(__name__)

# Swagger (/docs), ReDoc (/redoc) and /openapi.json list every endpoint
# and schema. They are OFF unless ENABLE_API_DOCS=true, so production is
# safe even when the variable is forgotten. Set it only in local/dev.
ENABLE_API_DOCS = os.getenv("ENABLE_API_DOCS", "false").strip().lower() == "true"

app = FastAPI(
    title="MultiAgentMerchant",
    description="AI-powered merchant and offer discovery API",
    version="1.0.0",
    docs_url="/docs" if ENABLE_API_DOCS else None,
    redoc_url="/redoc" if ENABLE_API_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_API_DOCS else None,
)

logger.info("api_docs enabled=%s", ENABLE_API_DOCS)
# One line per worker process at startup: check that every worker
# shows the same workers= value as uvicorn --workers.
logger.info(
    "server_settings pid=%s workers=%s llm_slots_per_worker=%s "
    "llm_slots_total=%s",
    os.getpid(), WORKER_COUNT,
    LLM_MAX_CONCURRENT_CALLS, LLM_EFFECTIVE_TOTAL_CALLS,
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



