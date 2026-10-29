
from app.agent.graph.state import Agentstate


def _format_result(result) -> str:
    """
    Agents return either a message string or a list of records
    (dicts). Turn either one into readable text.
    """
    if isinstance(result, str):
        return result

    if isinstance(result, list):
        blocks = []
        for record in result:
            if isinstance(record, dict):
                # Skip empty fields so the answer stays short.
                lines = [
                    f"{key}: {value}"
                    for key, value in record.items()
                    if value not in (None, "")
                ]
                blocks.append("\n".join(lines))
            else:
                blocks.append(str(record))
        return "\n\n".join(blocks)

    return str(result)


async def ResponseAgent(state: Agentstate) -> dict:
    # Step 1: Get results from each agent
    results = [
        state.get("merchant_results", ""),
        state.get("offer_results", ""),
        state.get("nearby_merchantresults", ""),
        state.get("redemption_results", ""),
    ]

    # Step 2: Remove empty results and format the rest as text
    valid_results = [
        _format_result(result)
        for result in results
        if result
    ]

    # Step 3: Combine the results into one string
    if valid_results:
        final_response = "\n\n".join(valid_results)
    else:
        final_response = "I couldn't find any relevant results."

    # Step 4: Return the final response to LangGraph
    return {
        "response": final_response
    }
