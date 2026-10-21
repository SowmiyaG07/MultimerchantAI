from fastapi import APIRouter


healthrouter = APIRouter(prefix="/health",tags=["health"])


@healthrouter.get("/")
def health_check():
    return {"status" : "Healthy"}