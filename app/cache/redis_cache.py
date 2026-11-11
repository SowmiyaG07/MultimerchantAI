import json
import os
import time
from dotenv import load_dotenv
from redis import asyncio as aioredis
from redis.exceptions import RedisError

from app.utilities.log import Applogger

load_dotenv()

logger = Applogger.get_loggerdata(__name__)


# key -> (count, window_reset_time). Used only while Redis is down.
_local_counters: dict[str, tuple[int, float]] = {}

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Redis is only a cache, so give up quickly and let SQL answer.
redis_client = aioredis.from_url(
    redis_url,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
)

# ---------------------------------------------
# Redis down handling
# ---------------------------------------------

# After a Redis failure, skip Redis for this many seconds so every
# request does not wait for the 2-second Redis timeout again.
REDIS_RETRY_SECONDS = 30

_redis_down_until = 0.0

# Sessions whose latest turn could not be cached. Their Redis history
# may be missing that turn, so it must not be used until it is cleared.
_stale_sessions: set[str] = set()


def _redis_skipped() -> bool:
    return time.monotonic() < _redis_down_until


def _mark_redis_down(action: str, error: Exception) -> None:
    global _redis_down_until
    _redis_down_until = time.monotonic() + REDIS_RETRY_SECONDS
    logger.warning(
        "redis_unavailable action=%s retry_in_seconds=%s error=%s",
        action, REDIS_RETRY_SECONDS, error,
    )
    
    
    
def _check_local_rate_limit(key: str, limit: int, window_seconds: int) -> int:
    now = time.monotonic()
    
    if len(_local_counters)>10000:
        for customer_key in list(_local_counters.keys()):
            customer_data = _local_counters[customer_key]
            
            count = customer_data[0]
            reset_time = customer_data[1]
            
            if reset_time <= now:
                del _local_counters[customer_key]
                
    if key in _local_counters:
        count = _local_counters[key][0]
        reset_at = _local_counters[key][1]
        
    else:
        count = 0
        reset_at = now + window_seconds
        
    if now >= reset_at:
        count = 0
        reset_at = now + window_seconds

    # Step 4: Increase the request count
    count = count + 1

    # Step 5: Save the updated customer information
    _local_counters[key] = (count, reset_at)

    # Step 6: Check whether the customer exceeded the limit
    if count > limit:

        remaining_seconds = int(reset_at - now)

        if remaining_seconds < 1:
            remaining_seconds = 1

        return remaining_seconds

    # Request is allowed
    return 0

        
                
                
             
# ---------------------------------------------
# Chat history cache
# ---------------------------------------------
# Redis only caches history; SQL always has every turn. So when Redis
# is down, every function here returns at once (no 2-second wait) and
# SQL answers instead.
#
# A turn that could not be appended leaves the Redis copy of that
# session OUT OF DATE (it is missing the latest question/answer).
# Such sessions are kept in _stale_sessions: their Redis copy is not
# read, and is deleted as soon as Redis is reachable again, so the next
# follow-up loads the full history from SQL.

MAX_STALE_SESSIONS = 10_000


def _history_key(session_id: str) -> str:
    return f"chat_history:{session_id}"


def _mark_stale(session_id: str) -> None:
    if len(_stale_sessions) >= MAX_STALE_SESSIONS:
        # Very long outage: stop tracking rather than grow forever.
        # Untracked copies still expire with their TTL (30 minutes).
        logger.warning("redis_stale_sessions_full max=%s", MAX_STALE_SESSIONS)
        return
    _stale_sessions.add(session_id)


async def _drop_stale_copy(session_id: str) -> bool:
    """
    Delete the out-of-date Redis copy of a stale session.
    Returns True when the session is clean (not stale, or deleted now).
    """
    if session_id not in _stale_sessions:
        return True

    try:
        await redis_client.delete(_history_key(session_id))
    except RedisError as error:
        _mark_redis_down("drop_stale", error)
        return False

    _stale_sessions.discard(session_id)
    logger.info("redis_stale_history_deleted")
    return True


async def load_history_from_redis(session_id: str | None) -> list[dict]:
    """Returns [] when Redis is down or the copy is stale: SQL answers."""
    if not session_id or _redis_skipped():
        return []

    # Stale copy: delete it and let SQL load the full history.
    if session_id in _stale_sessions:
        await _drop_stale_copy(session_id)
        return []

    try:
        return await _read_history(session_id)
    except RedisError as error:
        _mark_redis_down("load", error)
        return []


async def _read_history(session_id: str | None) -> list[dict]:
    if not session_id:
        return []

    raw_history = await redis_client.get(_history_key(session_id))

    if not raw_history:
        return []

    try:
        history = json.loads(raw_history)
    except ValueError:
        logger.warning("redis_bad_history_json")
        return []

    if isinstance(history, list):
        return history

    return []


async def _write_history(session_id: str, history: list[dict], ttl_seconds: int) -> bool:
    """Write the cached history. Returns False when Redis failed."""
    try:
        await redis_client.set(
            _history_key(session_id),
            json.dumps(history),
            ex=ttl_seconds,
        )
    except RedisError as error:
        _mark_redis_down("save", error)
        return False
    return True


async def save_history_to_redis(
    session_id: str | None,
    history: list[dict],
    ttl_seconds: int = 1800,
) -> None:
    """
    Cache a full history loaded from SQL. It is complete, so it also
    replaces a stale copy.
    """
    if not session_id or not history or _redis_skipped():
        return

    if await _write_history(session_id, history, ttl_seconds):
        _stale_sessions.discard(session_id)


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
    the last max_messages items are kept. If the turn cannot be added,
    the session is marked stale (see above).
    """

    if not session_id or not question or not answer:
        return

    # Redis down: no wait, but the cache now misses this turn.
    if _redis_skipped():
        _mark_stale(session_id)
        return

    # Stale copy: appending to it would keep it incomplete. Delete it;
    # the next follow-up rebuilds it from SQL (which has this turn).
    if session_id in _stale_sessions:
        await _drop_stale_copy(session_id)
        return

    # A failed read must not overwrite the cache with only this turn.
    try:
        history = await _read_history(session_id)
    except RedisError as error:
        _mark_redis_down("append", error)
        _mark_stale(session_id)
        return

    history.append({"role": "user", "content": question})
    history.append({"role": "assistant", "content": answer})

    if not await _write_history(session_id, history[-max_messages:], ttl_seconds):
        _mark_stale(session_id)

    
    
async def check_rate_limit(
    key: str,
    limit: int,
    window_seconds: int
) -> int:


    # Redis failed recently: do not wait for it again.
    if _redis_skipped():
        return _check_local_rate_limit(key, limit, window_seconds)

    try:
        async with redis_client.pipeline(transaction=True) as pipe:

            pipe.set(key, 0, ex=window_seconds, nx=True)

            pipe.incr(key)

            pipe.ttl(key)

            _, count, ttl = await pipe.execute()

    except RedisError as error:
        _mark_redis_down("rate_limit", error)
        return _check_local_rate_limit(key, limit, window_seconds)

    if count > limit:
        return max(ttl, 1)

    return 0