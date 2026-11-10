"""
Redemption limits:
- the database returns at most 20 rows, newest first
- TotalCount still has the number of ALL matching redemptions
- a date range longer than MAX_HISTORY_DAYS is shortened, and the
  user is told

The first two tests read the real redemption table (read-only).
Only counts are checked, no customer data is printed.
"""
from dotenv import load_dotenv

load_dotenv()

import asyncio  # noqa: E402
from datetime import date, datetime, timedelta  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

import app.agent.redemption_agent as redemption_agent  # noqa: E402
from app.agent.redemption_agent import (  # noqa: E402
    MAX_HISTORY_DAYS,
    MAX_LISTED_REDEMPTIONS,
    limit_date_range,
    redemptionagent,
)
from app.database.database import get_connection  # noqa: E402
from app.tools.redemption_tool import (  # noqa: E402
    MAX_REDEMPTION_ROWS,
    get_redemption_history,
)

WIDE_FROM = date(2000, 1, 1)


def customer_with_most_redemptions():
    connection = get_connection()
    db = next(connection)
    try:
        row = db.execute(text("""
            SELECT TOP 1 CustomerID, COUNT(*) AS n
            FROM [DigitalSavings].[dbo].[ALL_Get1CardLoadRedeem]
            WHERE Redeemed = 1 AND RedeemedOn >= :start
            GROUP BY CustomerID
            ORDER BY COUNT(*) DESC
        """), {"start": datetime(2000, 1, 1)}).first()
    finally:
        connection.close()
    if not row:
        pytest.skip("no redemptions in the database")
    return row.CustomerID, row.n


# ---------------------------------------------
# Real database (read-only)
# ---------------------------------------------

def test_database_returns_at_most_20_rows_with_total_count():
    customer_id, real_total = customer_with_most_redemptions()

    records = asyncio.run(get_redemption_history(customer_id, WIDE_FROM, date.today()))
    print(f"\nreal_total={real_total} rows_returned={len(records)}")

    assert len(records) == min(real_total, MAX_REDEMPTION_ROWS)
    assert all(r["TotalCount"] == real_total for r in records)

    # newest first
    dates = [r["RedeemedOn"] for r in records if r["RedeemedOn"]]
    assert dates == sorted(dates, reverse=True)


def test_limit_cannot_go_above_20():
    customer_id, real_total = customer_with_most_redemptions()
    records = asyncio.run(
        get_redemption_history(customer_id, WIDE_FROM, date.today(), limit=10_000)
    )
    assert len(records) <= MAX_REDEMPTION_ROWS


# ---------------------------------------------
# Date range limit
# ---------------------------------------------

def test_short_range_is_unchanged():
    to_date = date(2026, 11, 10)
    assert limit_date_range(date(2026, 10, 1), to_date) == (date(2026, 10, 1), False)


def test_long_range_is_shortened():
    to_date = date(2026, 11, 10)
    from_date, shortened = limit_date_range(date(2015, 1, 1), to_date)
    assert shortened is True
    assert from_date == to_date - timedelta(days=MAX_HISTORY_DAYS)


# ---------------------------------------------
# Agent: summary uses TotalCount, user is told about the shorter range
# ---------------------------------------------

def fake_rows(total, rows):
    return [{
        "TotalCount": total,
        "RedeemedOn": datetime(2026, 10, 30 - i % 28, 12, 0),
        "GrossSale": 10.0, "LocationID": i, "DBAName": f"Store {i}",
        "BusinessName": None, "City": "Tuscaloosa", "State": "AL",
        "OfferType": None, "Get1CardFreeOffer": "Free drink",
        "Get1CardAmount": None, "Get1MinPurchaseAmount": None,
    } for i in range(rows)]


def run_agent(monkeypatch, rows, from_date=None, to_date=None):
    calls = []

    # Same 4-argument signature the other tests' fakes use
    async def fake_history(customer_id, from_value, to_value, merchant_name=None):
        calls.append({"from": from_value, "to": to_value})
        return rows

    monkeypatch.setattr(redemption_agent, "get_redemption_history", fake_history)
    state = {
        "authenticated_customer_id": 90001,
        "redemption_agent_params": [{}],
        "from_date": from_date,
        "to_date": to_date,
    }
    return asyncio.run(redemptionagent(state)), calls


def test_summary_counts_all_redemptions_not_just_20(monkeypatch):
    result, calls = run_agent(monkeypatch, fake_rows(total=57, rows=20))
    summary = result["redemption_results"][0]["summary"]

    assert MAX_LISTED_REDEMPTIONS == MAX_REDEMPTION_ROWS == 20
    assert summary.startswith("You have 57 redemptions")
    assert "Showing the latest 20." in summary
    assert len(result["redemption_results"]) == 1 + 20      # summary + 20 rows


def test_long_range_is_searched_shortened_and_user_is_told(monkeypatch):
    result, calls = run_agent(
        monkeypatch, fake_rows(total=3, rows=3),
        from_date=date(2015, 1, 1), to_date=date(2026, 11, 10),
    )
    summary = result["redemption_results"][0]["summary"]

    assert calls[0]["from"] == date(2026, 11, 10) - timedelta(days=MAX_HISTORY_DAYS)
    assert f"up to {MAX_HISTORY_DAYS} days" in summary


def test_long_range_with_no_rows_also_tells_user(monkeypatch):
    result, _ = run_agent(
        monkeypatch, [], from_date=date(2015, 1, 1), to_date=date(2026, 11, 10),
    )
    assert "You have no redemptions" in result["redemption_results"]
    assert f"up to {MAX_HISTORY_DAYS} days" in result["redemption_results"]


def test_old_mock_without_total_count_still_works(monkeypatch):
    """Mocks in other tests return rows without TotalCount."""
    rows = fake_rows(total=0, rows=2)
    for r in rows:
        del r["TotalCount"]
    result, _ = run_agent(monkeypatch, rows)
    assert result["redemption_results"][0]["summary"].startswith("You have 2 redemptions")
