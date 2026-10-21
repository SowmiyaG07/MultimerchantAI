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


# app = FastAPI()


# @app.get("/test-db")
# def test_db():
#     connection_generator = get_connection()
#     db = next(connection_generator)

#     try:
#         db.execute(text("SELECT 1"))

#         return {
#             "status": "success",
#             "message": "Database connected successfully"
#         }

#     except Exception as e:
#         print("Database connection error:", repr(e))
#         return {
#             "status": "error",
#             "message": str(e)
#         }

#     finally:
#         connection_generator.close()
