import asyncio
from datetime import datetime

from app.database.database import get_connection
from sqlalchemy import text


def _load_history_rows(session_id: str, limit: int):
    connection = get_connection()

    db = next(connection)

    try:
        result = db.execute(
            text
            ("""
            SELECT TOP (:limit)
                question,
                answer
            FROM dbo.ConversationHistory
            WHERE session_id = :session_id
              AND answer IS NOT NULL
              AND status = 'success'
            ORDER BY created_at DESC
        """),
            {"session_id": session_id, "limit": limit},
        )

        return result.mappings().all()

    finally:
        connection.close()


def _insert_history_row(params: dict) -> None:
    connection = get_connection()

    db = next(connection)

    try:
        db.execute(
            text
            ("""
            INSERT INTO dbo.ConversationHistory (
                customer_id,
                session_id,
                trace_id,
                question,
                answer,
                created_at,
                completed_at,
                total_latency_ms,
                model_name,
                status,
                error_message
            )
            VALUES (
                :authenticated_customer_id,
                :session_id,
                :trace_id,
                :question,
                :answer,
                :created_at,
                :completed_at,
                :total_latency_ms,
                :model_name,
                :status,
                :error_message
            )
        """),
            params,
        )

        db.commit()

    finally:
        connection.close()


async def save_conversation_to_sql(
    authenticated_customer_id: int,
    session_id: str,
    trace_id: str,
    question: str,
    answer: str | None,
    created_at: datetime,
    completed_at: datetime,
    model_name: str | None,
    status: str = "success",
    error_message: str | None = None,
) -> None:
    """
    Save one question and answer to dbo.ConversationHistory.
    """

    total_latency_ms = int(
        (completed_at - created_at).total_seconds() * 1000
    )

    await asyncio.to_thread(
        _insert_history_row,
        {
            "authenticated_customer_id": authenticated_customer_id,
            "session_id": session_id,
            "trace_id": trace_id,
            "question": question,
            "answer": answer,
            "created_at": created_at,
            "completed_at": completed_at,
            "total_latency_ms": total_latency_ms,
            "model_name": model_name,
            "status": status,
            "error_message": error_message,
        },
    )


async def load_relevant_history_from_sql(
    session_id: str | None,
    message: str,
    max_tokens: int = 1500,
    max_turns: int = 10,
) -> list[dict]:
    """
    Load the latest conversation turns for a session from SQL.

    Returns role/content dictionaries in chronological order:
        [Q_old, A_old, ..., Q_new, A_new]

    This is the order expected by queryexpansion.conversation()
    and the order stored in Redis.
    The newest turns are kept first until the approximate token
    budget (about 4 characters per token) is used up.
    """

    if not session_id:
        return []

    rows = await asyncio.to_thread(
        _load_history_rows,
        session_id,
        max_turns,
    )

    # Rows arrive newest first, so the budget keeps the latest turns.
    turns = []
    used_tokens = 0

    for row in rows:
        question = row["question"] or ""
        answer = row["answer"] or ""

        turn_tokens = (len(question) + len(answer)) // 4

        if turns and used_tokens + turn_tokens > max_tokens:
            break

        used_tokens += turn_tokens

        turns.append((question, answer))

    history = []

    for question, answer in reversed(turns):
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})

    return history
