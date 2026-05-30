import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from langchain.agents import create_agent
from langchain.messages import HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import InMemorySaver


# Load .env for local dev only (Railway injects env vars directly)
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path="../.env", override=True)
except ImportError:
    pass


def get_mcp_config():
    """Build MCP server config with environment-based settings"""
    config = {
        "kiwi": {
            "transport": "streamable_http",
            "url": "https://mcp.kiwi.com",
        }
    }

    # TODO: Duffel MCP has bug with stdio transport in production
    # Re-enable when fixed or switch to HTTP transport
    # duffel_key = os.getenv("DUFFEL_API_KEY_LIVE")
    # if duffel_key:
    #     config["duffel"] = {
    #         "transport": "stdio",
    #         "command": "uvx",
    #         "args": ["flights-mcp"],
    #         "env": {
    #             "DUFFEL_API_KEY_LIVE": duffel_key
    #         }
    #     }

    return config


client = MultiServerMCPClient(get_mcp_config())

agent = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    global agent

    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is missing. Add it to your .env file."
        )

    tools = await client.get_tools()

    agent = create_agent(
        model="openai:gpt-5-nano",
        tools=tools,
        checkpointer=InMemorySaver(),
        system_prompt=SYSTEM_PROMPT,
    )

    yield

    # Shutdown (cleanup if needed)


app = FastAPI(title="Flight Search AI API", lifespan=lifespan)

# Serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")


SYSTEM_PROMPT = """
You are a friendly flight-search assistant. Help users find the cheapest flights by asking for required details conversationally.

**Required information to search:**
1. Origin airport or city (where flying from)
2. Departure date (format: YYYY-MM-DD or natural like "June 15")
3. Number of passengers (default to 1 if not mentioned)

**Optional information:**
- Destination (default: Portland, Oregon, PDX if not specified)
- Return date (default: 5 days after departure for round trip)
- Trip type (default: round trip)
- Cabin class (economy/premium_economy/business/first)

**Conversation flow:**
1. Greet user and ask what's missing from: origin, departure date, number of passengers
2. Ask ONE question at a time - don't overwhelm
3. Once you have origin + departure date + passengers, search immediately
4. If user says "find flights" without details, ask: "Where are you flying from and when?"

**Search behavior:**
- You have access to MULTIPLE flight search sources (Kiwi.com and Duffel)
- Query ALL available sources in parallel to compare prices
- Always use flight-search tools - never invent prices, airlines, or schedules
- Optimize for cheapest total price across all sources
- All passengers travel together from same origin
- If price unavailable from one source, check others
- Show best option regardless of which source provided it

**Output format after search:**

🎯 **Best Option Found** (from [Source Name])

**Flight Summary:**
- Route: [Origin] → [Destination] (round trip)
- Dates: [Departure date] - [Return date]
- Passengers: [count]
- Cabin: [class]
- Total Price: $[total] ($[per passenger] per person)

**Outbound Flight:**
- Airline: [name]
- Flight: [number]
- Departure: [time] from [airport]
- Arrival: [time] at [airport]
- Duration: [hours]
- Stops: [number and airports if any]

**Return Flight:**
[same details]

**Alternatives:**
[Show 1-2 other cheap options from any source if available]

**Note:** Prices may change. Verify baggage rules and booking terms before purchasing.

Keep responses concise, friendly, and helpful.
"""


class FlightSearchRequest(BaseModel):
    origin: str = Field(..., description="Origin city or airport, for example BNA or Nashville")
    departure_date: str = Field(..., description="Departure date, for example 2026-03-31")
    passengers: int = Field(default=1, ge=1, description="Number of passengers")
    destination: str = Field(default="Portland, Oregon, PDX")
    return_date: Optional[str] = Field(default=None, description="Return date, for example 2026-04-05")
    trip_length_days: int = Field(default=5, ge=1, description="Used if return_date is not provided")


class ChatRequest(BaseModel):
    message: str = Field(..., description="User's message")
    thread_id: Optional[str] = Field(default="default", description="Conversation thread ID for session tracking")




@app.get("/")
def home():
    """Serve the web UI"""
    return FileResponse("static/index.html")


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "message": "Flight Search AI API is running",
    }


@app.post("/chat")
async def chat(
    request: ChatRequest,
    x_app_token: Optional[str] = Header(default=None),
):
    """Chat endpoint - multi-turn conversation for flight search"""
    expected_token = os.getenv("FLIGHT_APP_TOKEN")

    if expected_token:
        if x_app_token != expected_token:
            raise HTTPException(status_code=401, detail="Unauthorized")

    if agent is None:
        raise HTTPException(status_code=500, detail="Agent is not initialized")

    response = await agent.ainvoke(
        {"messages": [HumanMessage(content=request.message)]},
        config={"configurable": {"thread_id": request.thread_id}},
    )

    return {
        "thread_id": request.thread_id,
        "message": response["messages"][-1].content,
    }


@app.post("/search-flights")
async def search_flights(
    request: FlightSearchRequest,
    x_app_token: Optional[str] = Header(default=None),
):
    """Direct search endpoint - structured request with all params"""
    expected_token = os.getenv("FLIGHT_APP_TOKEN")

    if expected_token:
        if x_app_token != expected_token:
            raise HTTPException(status_code=401, detail="Unauthorized")

    if agent is None:
        raise HTTPException(status_code=500, detail="Agent is not initialized")

    return_date_text = (
        request.return_date
        if request.return_date
        else f"approximately {request.trip_length_days} days after the departure date"
    )

    user_request = f"""
Find the cheapest round-trip flights.

Origin: {request.origin}
Destination: {request.destination}
Number of passengers: {request.passengers}
Trip type: Round trip
Departure date: {request.departure_date}
Return date: {return_date_text}
Optimization preference: cheapest total price

Important:
- All passengers are traveling from the same origin.
- Do not search separate origins per passenger.
- Search for the cheapest total flight option.
- Show price per passenger and estimated total price for all passengers when available.
- Format the answer clearly using sections and tables.
"""

    response = await agent.ainvoke(
        {"messages": [HumanMessage(content=user_request)]},
        config={"configurable": {"thread_id": "flight-search-api"}},
    )

    return {
        "request": {
            "origin": request.origin,
            "destination": request.destination,
            "passengers": request.passengers,
            "departure_date": request.departure_date,
            "return_date": request.return_date,
            "trip_length_days": request.trip_length_days,
        },
        "answer": response["messages"][-1].content,
    }