import asyncio
from pprint import pprint

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.messages import HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import InMemorySaver


SYSTEM_PROMPT = """
You are an expert flight-search and travel-planning assistant.

Your job is to find the cheapest available flight options using the provided tools and explain them in a clean, formatted way.

Default destination:
- Portland, Oregon, USA
- Use Portland International Airport, PDX, unless the user explicitly provides another destination.

Trip type:
- Assume round trip unless the user explicitly says one way.
- The return date may be approximately 5 days after the departure date.
- If the user gives a trip length instead of a return date, calculate the approximate return date from the departure date.

Passenger rules:
- All passengers depart from the same origin airport or city.
- Do not ask for separate origin cities for each passenger.
- Search using the total number of passengers when possible.
- If the tool does not support passenger count, still report pricing clearly and mention whether the price appears to be per passenger or total.

Optimization priority:
1. Cheapest total price
2. Correct origin and destination
3. Correct departure and return dates
4. Reasonable flight duration
5. Practical layovers
6. Fewer stops
7. Reasonable departure and arrival times

Use the available flight-search tools before giving recommendations.

Do not invent:
- Prices
- Airlines
- Flight numbers
- Schedules
- Seat availability
- Baggage rules
- Booking links

If a price is unavailable, say price unavailable.
If baggage details are unavailable, say baggage details unavailable.
Treat flight prices and availability as time-sensitive.

For each flight option, include when available:
- Airline
- Flight number
- Origin airport
- Destination airport
- Departure date and time
- Arrival date and time
- Return departure date and time
- Return arrival date and time
- Number of stops
- Layover airports and durations
- Total duration
- Price per passenger
- Estimated total price for all passengers
- Booking/provider information if available

Preferred output format:

# Cheapest Flight Recommendation

## Search Summary
- Origin:
- Destination:
- Passengers:
- Trip type:
- Departure date:
- Return date:
- Sort priority: Cheapest total price

## Best Cheapest Option
Provide a simple table with:
Airline | Route | Dates | Stops | Duration | Price per passenger | Total price

## Flight Details
Show outbound and return details separately.

## Other Cheap Alternatives
Show up to 3 alternatives if available.

## Notes
Mention price changes, baggage limitations, refund/change rules if unknown, and that the user should verify final details before booking.

Keep the answer concise, formatted, and easy to read.
Do not ask follow-up questions unless the request cannot be completed without missing information.
"""


def ask_required(prompt: str) -> str:
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("Please enter a value.")


def ask_int(prompt: str, minimum: int = 1) -> int:
    while True:
        value = input(prompt).strip()
        try:
            number = int(value)
            if number >= minimum:
                return number
            print(f"Please enter a number greater than or equal to {minimum}.")
        except ValueError:
            print("Please enter a valid number.")


def build_user_request() -> str:
    print("\nFlight Search Assistant")
    print("-----------------------")

    origin = ask_required(
        "Origin city or airport, for example BNA, Nashville, Dallas, or SFO: "
    )

    destination = input(
        "Destination city or airport [Portland, Oregon / PDX]: "
    ).strip()

    if not destination:
        destination = "Portland, Oregon, PDX"

    passenger_count = ask_int("Number of passengers: ", minimum=1)

    departure_date = ask_required(
        "Departure date, for example March 31, 2026 or 2026-03-31: "
    )

    return_choice = input(
        "Return date, or press Enter to use approximately 5 days later: "
    ).strip()

    if return_choice:
        return_date_text = return_choice
    else:
        return_date_text = "approximately 5 days after the departure date"

    preference = input("Optimization preference [cheapest]: ").strip()

    if not preference:
        preference = "cheapest total price"

    return f"""
Find the cheapest round-trip flights.

Origin: {origin}
Destination: {destination}
Number of passengers: {passenger_count}
Trip type: Round trip
Departure date: {departure_date}
Return date: {return_date_text}
Optimization preference: {preference}

Important:
- All passengers are traveling from the same origin.
- Do not search separate origins per passenger.
- Find the cheapest flight option.
- We want to find flights less than $1000 overall.
- Show price per passenger and estimated total price for all passengers when available.
- Format the answer clearly using tables and sections.
"""


def extract_text_from_chunk(token) -> str:
    """
    Handles different LangChain message chunk shapes.
    Returns only normal text chunks, ignoring tool-call chunks.
    """
    text_parts = []

    content_blocks = getattr(token, "content_blocks", None)

    if content_blocks:
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        return "".join(text_parts)

    content = getattr(token, "content", None)

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        return "".join(text_parts)

    return ""


async def stream_agent_response(agent, user_request: str, config: dict) -> str:
    """
    Streams the final answer to the terminal as the model generates it.
    Also returns the full final text.
    """
    print("\nFinal answer:")
    print("-------------")

    final_text_parts = []

    async for chunk in agent.astream(
        {"messages": [HumanMessage(content=user_request)]},
        config=config,
        stream_mode="messages",
    ):
        token = None

        # Newer LangChain often returns: (token, metadata)
        if isinstance(chunk, tuple) and len(chunk) == 2:
            token, metadata = chunk

        # Some event formats return dict objects.
        elif isinstance(chunk, dict):
            if chunk.get("type") == "messages":
                data = chunk.get("data", [])
                if isinstance(data, tuple) and len(data) == 2:
                    token, metadata = data
                elif isinstance(data, list) and len(data) == 2:
                    token, metadata = data

        if token is None:
            continue

        text = extract_text_from_chunk(token)

        if text:
            print(text, end="", flush=True)
            final_text_parts.append(text)

    print("\n")
    return "".join(final_text_parts)


async def main():
    load_dotenv(dotenv_path="../.env", override=True)

    user_request = build_user_request()

    client = MultiServerMCPClient(
        {
            "travel_server": {
                "transport": "streamable_http",
                "url": "https://mcp.kiwi.com",
            }
        }
    )

    tools = await client.get_tools()

    agent = create_agent(
        model="openai:gpt-5-nano",
        tools=tools,
        checkpointer=InMemorySaver(),
        system_prompt=SYSTEM_PROMPT,
    )

    config = {"configurable": {"thread_id": "cheapest-flight-search-session-1"}}

    print("\nUser request sent to agent:")
    print("---------------------------")
    print(user_request)

    final_answer = await stream_agent_response(
        agent=agent,
        user_request=user_request,
        config=config,
    )

    print("\nSaved final answer:")
    print("-------------------")
    print(final_answer)


if __name__ == "__main__":
    asyncio.run(main())