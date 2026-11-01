from app.guardrails.inputguardrails import drop_invalid_state

from app.agent.graph.state import Agentstate

from app.utilities.StateMapping import _WEEKDAY_COLUMNS
from app.tools.offer_search_tool import searchoffer_data
from app.utilities.log import Applogger

logger = Applogger.get_loggerdata(__name__)


def _offer_valid_days(record: dict) -> str | None:
    """Return the days on which the offer is valid."""
    days = []
    for column, day in _WEEKDAY_COLUMNS:
        if record.get(column):
            days.append(day)
    if not days:
        return None
    return ",".join(days)


def _to_llm_view(record: dict) -> dict:
    """Project a database record into user-relevant offer fields."""

    return {
        "id": record["LocationID"],
        "name": record["DBAName"] or record["BusinessName"],
        "city": record["City"],
        "state": record["State"],
        "offer": record["Get1CardFreeOffer"],
        "offer_type": record["OfferTypeLabel"],
        "amount": record["Get1CardAmount"],
        "min_purchase": record["Get1MinPurchaseAmount"],
        "offer_details": record["TheScoop"],
        "offer_valid_until": record["OfferEndDate"],
        "offer_valid_days": _offer_valid_days(record),
        "offer_redeem_start_time": record.get("RedeemOfferStartTime"),
        "offer_redeem_end_time": record.get("RedeemOfferEndTime"),
    }


async def offer_agent(state: Agentstate) -> dict:
    """
    Offer Agent:

    Does not call an LLM. Reads offer criteria from the Supervisor,
    calls the offer tool and returns offer_results. Never runs an
    unfiltered search.

    1. Reads offer search parameters from Agentstate.
    2. Validates and normalizes parameters.
    3. Executes the existing SQL service.
    4. Projects database rows into offer results.
    5. Returns results to LangGraph state.
    """

    offer_search_params = state.get("offer_search_params", [])

    if not offer_search_params:
        return {"offer_results": ""}
    all_results = []
    searched = False

    for params in offer_search_params:
        name = params.get("name")
        offer_type = params.get("offer_type")
        min_percent = params.get("min_percent")
        max_percent = params.get("max_percent")
        city = params.get("city")
        state_name = params.get("state", "")
        state_name = drop_invalid_state(state_name)
        keyword = params.get("keyword")
        limit = params.get("limit", 10)

        has_criteria = False

        if name not in (None, ""):
            has_criteria = True

        if offer_type not in (None, ""):
            has_criteria = True

        if min_percent not in (None, ""):
            has_criteria = True

        if max_percent not in (None, ""):
            has_criteria = True

        if city not in (None, ""):
            has_criteria = True

        if state_name not in (None, ""):
            has_criteria = True

        if keyword not in (None, ""):
            has_criteria = True

        if not has_criteria:
            continue

        # Step 9: A valid search is going to be performed
        searched = True
        # Step 10: Call the offer search tool
        try:
            records = await searchoffer_data(
                name=name,
                offer_type=offer_type,
                min_percent=min_percent,
                max_percent=max_percent,
                city=city,
                state=state_name,
                keyword=keyword,
                limit=limit
            )
        except Exception:
            # Database or other errors: don't crash the whole graph.
            logger.exception("offer_search_failed")
            message = "Sorry, I couldn't search offers right now."
            print("OFFER RESULTS:", message)
            return {
                "offer_results": message,
                "all_agent_results": [
                    {
                        "agent": "OfferAgent",
                        "results": message
                    }
                ]
            }

        # Step 11: Convert each result and store it
        for record in records:
            result = _to_llm_view(record)
            all_results.append(result)

      # Step 12: Check whether any search was performed
    if not searched:
        logger.info("offer_search_skipped reason=no_criteria")
        message = (
            "Please provide more details, such as "
            "a merchant name, city or offer type."
        )
        print("OFFER RESULTS:", message)
        return {
            "offer_results": message,
            "all_agent_results": [
                {
                    "agent": "OfferAgent",
                    "results": message
                }
            ]
        }

    # Step 13: Check whether any offers were found
    if not all_results:
        logger.info("offer_search results=0")
        message = "No matching offers were found."
        print("OFFER RESULTS:", message)
        return {
            "offer_results": message,
            "all_agent_results": [
                {
                    "agent": "OfferAgent",
                    "results": message
                }
            ]
        }


    logger.info("offer_search results=%d", len(all_results))

    # Step 14: Return all matching offers
    print("OFFER RESULTS:", all_results)
    return {
        "offer_results": all_results,
        "all_agent_results": [
        {
            "agent": "OfferAgent",
            "results": all_results
        }
    ]
    }
