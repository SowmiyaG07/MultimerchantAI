from fastapi import APIRouter,Depends,HTTPException,status
from app.api.schema.chatrequest import chatresponse,chatrequest
from app.services.customer_service import getcustomerid_email,getcustomerid
import uuid
from app.api.dependencies import get_current_customer_id


chatrouter = APIRouter(tags=["chat"])


@chatrouter.post("/chat",response_model=chatresponse)
async def chat(request:chatrequest,customer_id : int = Depends(get_current_customer_id),):
    trace_id = str(uuid.uuid4())
    try :
        customerid = request.customer_id
        
        customer = getcustomerid(customerid)
        if customer is None:
                raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "CUSTOMER_NOT_FOUND",
                    "message": "The authenticated customer no longer exists.",
                    "trace_id": trace_id,
                },
            )

        if not customer["external_id"]:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "MISSING_EXTERNAL_ID",
                    "message": "Customer record has no ExternalID configured.",
                    "trace_id": trace_id,
                },
            )
        session_id = customer["external_id"]
        
        return True
    
    except HTTPException:
        raise
    except Exception as exc:
        print(
            f"[{trace_id}] CHAT ERROR:"
            f"{type(exc).__name__}:{exc}"
        )
        raise HTTPException(
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "CHAT_REQUEST_FAILED",
                "message": (
                    "An error occurred while processing "
                    "the chat request."
                ),
                "trace_id": trace_id,
            }
            
        )
        
        
    


