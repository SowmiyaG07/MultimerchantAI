import json
import os

from dotenv import load_dotenv
from redis import asyncio as aioredis

load_dotenv()

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

redis_client = aioredis.from_url(redis_url, decode_responses=True)


async def load_history_from_redis(session_id: str | None) -> list[dict]:
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

    await redis_client.set(
        key,
        json.dumps(history),
        ex=ttl_seconds,
    )


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

    history = await load_history_from_redis(session_id)

    history.append({"role": "user", "content": question})
    history.append({"role": "assistant", "content": answer})

    await save_history_to_redis(
        session_id=session_id,
        history=history[-max_messages:],
        ttl_seconds=ttl_seconds,
    )
