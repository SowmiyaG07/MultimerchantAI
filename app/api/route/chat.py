import asyncio

from fastapi import APIRouter,Depends,HTTPException,status
from app.api.schema.chatrequest import chatresponse,chatrequest
from app.services.customer_service import getcustomerid_email,getcustomerid
import uuid
from datetime import datetime
from app.api.dependencies import check_chat_rate_limit
from app.agent.munomultiagent import ollama_model
from app.agent.graph.workflow import multiagent
from app.services.history_service import save_conversation_to_sql
from app.cache.redis_cache import append_conversation_to_redis
from app.utilities.log import Applogger
from app.agent.llm_limits import LLMBusyError, LLM_QUEUE_TIMEOUT_SECONDS


chatrouter = APIRouter(tags=["chat"])

logger = Applogger.get_loggerdata(__name__)


@chatrouter.post("/chat",response_model=chatresponse)
async def chat(request:chatrequest,customerid : int = Depends(check_chat_rate_limit),):
    trace_id = str(uuid.uuid4())
    try :
        
        
        customer = await asyncio.to_thread(getcustomerid, customerid)
        if customer is None:
                raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "CUSTOMER_NOT_FOUND",
                    "message": "The authenticated customer no longer exists.",
                    "trace_id": trace_id,
                },
            )
        # 2. Check whether customer is active
        if not customer["active"]:
            raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
            "code": "CUSTOMER_INACTIVE",
            "message": "Your account is inactive. Please contact support.",
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
        
        initial_state = {
            "message": request.message,
            "session_id": session_id,
            "authenticated_customer_id": customerid,
            "latitude": request.latitude,
            "longitude": request.longitude,
            "from_date": request.from_date,
            "to_date": request.to_date,
        }
        
        created_at = datetime.now()
        
        config = {
    "run_name": "munomerchant_chat_request",
    "tags": [
        "munomerchant-ai",
        "fastapi",
    ],
    "metadata": {
        "trace_id": trace_id,
        "customer_id": str(customerid),
    },
    "configurable": {
        "thread_id": session_id,
    },
}

        result = await multiagent.ainvoke(initial_state,config=config,)

    

        answer = result.get("response","")

        # Any failure in this request (LLM, DB, Google) -> not a valid
        # answer for follow-ups. Saved for tracking, never used as history.
        errors = result.get("errors") or []
        history_status = "error" if errors else "success"

        logger.info(
            "chat_completed trace_id=%s customer_id=%s agents=%s status=%s errors=%s duration_ms=%.0f",
            trace_id,
            customerid,
            result.get("selected_agent"),
            history_status,
            errors,
            (datetime.now() - created_at).total_seconds() * 1000,
        )

        # Save the turn so the next follow-up can use it.
        # A failed save must not fail the chat response.
        try:
            await save_conversation_to_sql(
                 authenticated_customer_id=customerid,
                session_id=session_id,
                trace_id=trace_id,
                question=request.message,
                answer=answer,
                created_at=created_at,
                completed_at=datetime.now(),
                model_name=ollama_model,
                status=history_status,
                error_message=",".join(errors) or None,
            )

            # Redis holds the history used for follow-ups: success only.
            if not errors:
                await append_conversation_to_redis(
                    session_id=session_id,
                    question=request.message,
                    answer=answer,
                )

        except Exception:
            logger.exception(
                "history_save_failed trace_id=%s customer_id=%s",
                trace_id, customerid,
            )

        return {
            "session_id"   : session_id,
            "answer" : answer,
            "trace_id" : trace_id
        }
    
    except HTTPException:
        raise
    except LLMBusyError:
        logger.warning(
            "chat_llm_busy trace_id=%s customer_id=%s",
            trace_id, customerid,
        )
        retry_after = int(LLM_QUEUE_TIMEOUT_SECONDS) or 1
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "LLM_BUSY",
                "message": "Our AI assistant is currently handling multiple requests. Please try again shortly.",
                "retry_after_seconds": retry_after,
                "trace_id": trace_id,
            },
            headers={"Retry-After": str(retry_after)},
        )
    except Exception:
        logger.exception(
            "chat_failed trace_id=%s customer_id=%s",
            trace_id, customerid,
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
        
        
    


