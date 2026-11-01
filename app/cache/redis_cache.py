import json
import os

from dotenv import load_dotenv
from redis import asyncio as aioredis
from redis.exceptions import RedisError

from app.utilities.log import Applogger

load_dotenv()

logger = Applogger.get_loggerdata(__name__)

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Redis is only a cache, so give up quickly and let SQL answer.
redis_client = aioredis.from_url(
    redis_url,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
)


async def load_history_from_redis(session_id: str | None) -> list[dict]:
    """Returns [] when Redis is down, so the caller falls back to SQL."""
    try:
        return await _read_history(session_id)
    except RedisError as error:
        logger.warning("redis_unavailable action=load error=%s", error)
        return []


async def _read_history(session_id: str | None) -> list[dict]:
    if not session_id:
        return []

    key = f"chat_history:{session_id}"

    raw_history = await redis_client.get(key)

    if not raw_history:
        return []

    history = json.loads(raw_history)

    if isinstance(history, list):
        return history

    return []


async def save_history_to_redis(
    session_id: str | None,
    history: list[dict],
    ttl_seconds: int = 1800,
) -> None:

    if not session_id or not history:
        return

    key = f"chat_history:{session_id}"

    try:
        await redis_client.set(
            key,
            json.dumps(history),
            ex=ttl_seconds,
        )
    except RedisError as error:
        logger.warning("redis_unavailable action=save error=%s", error)


async def append_conversation_to_redis(
    session_id: str | None,
    question: str,
    answer: str,
    max_messages: int = 20,
    ttl_seconds: int = 1800,
) -> None:
    """
    Append the latest question and answer to the cached history.

    History stays in chronological order (oldest first), and only
    the last max_messages items are kept.
    """

    if not session_id or not question or not answer:
        return

    # A failed read must not overwrite the cache with only this turn.
    try:
        history = await _read_history(session_id)
    except RedisError as error:
        logger.warning("redis_unavailable action=append error=%s", error)
        return

    history.append({"role": "user", "content": question})
    history.append({"role": "assistant", "content": answer})

    await save_history_to_redis(
        session_id=session_id,
        history=history[-max_messages:],
        ttl_seconds=ttl_seconds,
    )
