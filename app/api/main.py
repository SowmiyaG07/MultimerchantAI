from fastapi import FastAPI, APIRouter

from app.api.route.auth import authrouter
from app.api.route.chat import chatrouter
from app.api.route.health import healthrouter

from pydantic import BaseModel
from app.database.database import get_connection
from sqlalchemy import text

app = FastAPI(
    title="MultiAgentMerchant",
    description="AI-powered merchant and offer discovery API",
    version="1.0.0"

)


app.router.include_router(
    authrouter, prefix="/api/v1/auth", tags=["Authentication"])
app.router.include_router(chatrouter, prefix="/api/v1", tags=["chat"])
app.router.include_router(
    healthrouter, prefix="/api/v1/health", tags=["health"])



