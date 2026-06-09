import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from duffel_client import DuffelClient, format_duffel_for_display
from datetime import datetime, timedelta
import re
import asyncio


# City to airport code mapping (primary airport for each city)
CITY_TO_AIRPORT = {
    # Major US cities
    "nashville": "BNA",
    "raleigh": "RDU",
    "raleigh durham": "RDU",
    "portland": "PDX",
    "atlanta": "ATL",
    "chicago": "ORD",
    "dallas": "DFW",
    "houston": "IAH",
    "los angeles": "LAX",
    "la": "LAX",
    "new york": "JFK",
    "nyc": "JFK",
    "new york city": "JFK",
    "miami": "MIA",
    "orlando": "MCO",
    "phoenix": "PHX",
    "seattle": "SEA",
    "san francisco": "SFO",
    "sf": "SFO",
    "boston": "BOS",
    "denver": "DEN",
    "las vegas": "LAS",
    "vegas": "LAS",
    "washington": "DCA",
    "dc": "DCA",
    "washington dc": "DCA",
    "charlotte": "CLT",
    "philadelphia": "PHL",
    "philly": "PHL",
    "detroit": "DTW",
    "minneapolis": "MSP",
    "tampa": "TPA",
    "austin": "AUS",
    "san diego": "SAN",

    # More US cities
    "salt lake city": "SLC",
    "baltimore": "BWI",
    "pittsburgh": "PIT",
    "cincinnati": "CVG",
    "cleveland": "CLE",
    "columbus": "CMH",
    "indianapolis": "IND",
    "milwaukee": "MKE",
    "kansas city": "MCI",
    "st louis": "STL",
    "saint louis": "STL",
    "memphis": "MEM",
    "new orleans": "MSY",
    "jacksonville": "JAX",
    "fort lauderdale": "FLL",
    "fort myers": "RSW",
    "san jose": "SJC",
    "oakland": "OAK",
    "sacramento": "SMF",
    "san antonio": "SAT",
    "el paso": "ELP",
    "albuquerque": "ABQ",
    "tucson": "TUS",
    "honolulu": "HNL",
    "anchorage": "ANC",
    "boise": "BOI",
    "spokane": "GEG",
    "reno": "RNO",
    "omaha": "OMA",
    "tulsa": "TUL",
    "oklahoma city": "OKC",
    "little rock": "LIT",
    "birmingham": "BHM",
    "louisville": "SDF",
    "richmond": "RIC",
    "norfolk": "ORF",
    "buffalo": "BUF",
    "rochester": "ROC",
    "syracuse": "SYR",
    "albany": "ALB",
    "providence": "PVD",
    "hartford": "BDL",
    "burlington": "BTV",
    "manchester": "MHT",
    "portland maine": "PWM",
    "des moines": "DSM",
    "wichita": "ICT",
    "madison": "MSN",
    "grand rapids": "GRR",
    "knoxville": "TYS",
    "greenville": "GSP",
    "charleston": "CHS",
    "savannah": "SAV",
    "asheville": "AVL",
    "myrtle beach": "MYR",

    # Neighboring/alternate airports
    "laguardia": "LGA",
    "newark": "EWR",
    "burbank": "BUR",
    "ontario": "ONT",
    "orange county": "SNA",
    "santa ana": "SNA",
    "midway": "MDW",
    "hobby": "HOU",
    "love field": "DAL",
    "reagan": "DCA",
    "dulles": "IAD",
    "baltimore washington": "BWI",

    # International (major)
    "london": "LHR",
    "paris": "CDG",
    "tokyo": "NRT",
    "toronto": "YYZ",
    "vancouver": "YVR",
    "montreal": "YUL",
    "mexico city": "MEX",
    "cancun": "CUN",
    "cabo": "SJD",
    "cabo san lucas": "SJD",
}


def parse_airport_code(location: str) -> str:
    """Convert city name or airport code to IATA code"""
    location_clean = location.strip().upper()

    # Already a 3-letter code
    if len(location_clean) == 3 and location_clean.isalpha():
        return location_clean

    # Check city mapping (case-insensitive)
    location_lower = location.strip().lower()
    if location_lower in CITY_TO_AIRPORT:
        return CITY_TO_AIRPORT[location_lower]

    # Try first word if multi-word (e.g., "Los Angeles" -> check "los angeles" first, then "los")
    if ' ' in location_lower:
        if location_lower in CITY_TO_AIRPORT:
            return CITY_TO_AIRPORT[location_lower]

    # Last resort: take first 3 letters
    return location_clean[:3]


# Load .env for local dev only (Railway injects env vars directly)
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path="../.env", override=True)
except ImportError:
    pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup - verify Duffel key
    if not os.getenv("DUFFEL_API_KEY_LIVE"):
        raise RuntimeError("DUFFEL_API_KEY_LIVE is missing")

    yield

    # Shutdown (cleanup if needed)


app = FastAPI(title="Flight Search AI API - Duffel Powered", lifespan=lifespan)

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
    destination: str = Field(..., description="Destination city or airport, for example PDX or Portland")
    departure_date: str = Field(..., description="Departure date, for example 2026-03-31")
    passengers: int = Field(default=1, ge=1, description="Number of passengers")
    return_date: Optional[str] = Field(default=None, description="Return date, for example 2026-04-05")
    trip_length_days: int = Field(default=5, ge=1, description="Used if return_date is not provided")
    sort_by: str = Field(default="total_amount", description="Sort by: total_amount (cheapest) or total_duration (fastest)")
    max_budget_per_passenger: Optional[float] = Field(default=None, description="Max budget per passenger in USD")
    flexible_dates: bool = Field(default=False, description="Search ±3 days around departure date")


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


@app.post("/search-dual")
async def search_dual(request: FlightSearchRequest):
    """Search flights using Duffel API"""

    # Calculate return date if needed
    return_date = request.return_date
    if not return_date:
        dep_date = datetime.strptime(request.departure_date, "%Y-%m-%d")
        ret_date = dep_date + timedelta(days=request.trip_length_days)
        return_date = ret_date.strftime("%Y-%m-%d")

    # Parse origin and destination to airport codes
    origin_code = parse_airport_code(request.origin)
    dest_code = parse_airport_code(request.destination)

    try:
        duffel = DuffelClient()

        # Flexible dates: search ±3 days
        if request.flexible_dates:
            base_date = datetime.strptime(request.departure_date, "%Y-%m-%d")

            # Generate date range
            search_tasks = []
            date_map = {}

            for offset in range(-3, 4):  # -3, -2, -1, 0, 1, 2, 3
                search_date = base_date + timedelta(days=offset)
                search_date_str = search_date.strftime("%Y-%m-%d")

                # Calculate corresponding return date
                return_offset_date = datetime.strptime(return_date, "%Y-%m-%d") + timedelta(days=offset)
                return_offset_str = return_offset_date.strftime("%Y-%m-%d")

                date_map[search_date_str] = return_offset_str

                # Create search task
                task = duffel.search_flights(
                    origin=origin_code,
                    destination=dest_code,
                    departure_date=search_date_str,
                    return_date=return_offset_str,
                    passengers=request.passengers,
                    sort_by=request.sort_by,
                    max_budget_per_passenger=request.max_budget_per_passenger
                )
                search_tasks.append((search_date_str, return_offset_str, task))

            # Execute all searches in parallel
            results = await asyncio.gather(*[task for _, _, task in search_tasks], return_exceptions=True)

            # Collect all offers with date info
            all_offers = []
            for i, ((dep_date_str, ret_date_str, _), result) in enumerate(zip(search_tasks, results)):
                if isinstance(result, Exception):
                    continue

                offers = result.get("offers", [])
                for offer in offers:
                    # Annotate with search date
                    offer["search_departure_date"] = dep_date_str
                    offer["search_return_date"] = ret_date_str
                    all_offers.append(offer)

            # Sort by price
            all_offers.sort(key=lambda x: float(x.get("total_price", "USD 9999").split()[1]))

            return {
                "origin": request.origin,
                "destination": request.destination,
                "departure_date": request.departure_date,
                "return_date": return_date,
                "passengers": request.passengers,
                "flexible_dates": True,
                "offers": all_offers[:10],  # Top 10 across all dates
                "error": None,
                "message": f"Searched {len(search_tasks)} date combinations"
            }

        # Standard single-date search
        result = await duffel.search_flights(
            origin=origin_code,
            destination=dest_code,
            departure_date=request.departure_date,
            return_date=return_date,
            passengers=request.passengers,
            sort_by=request.sort_by,
            max_budget_per_passenger=request.max_budget_per_passenger
        )

        return {
            "origin": request.origin,
            "destination": request.destination,
            "departure_date": request.departure_date,
            "return_date": return_date,
            "passengers": request.passengers,
            "flexible_dates": False,
            "offers": result.get("offers", []),
            "error": result.get("error"),
            "message": result.get("message")
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Duffel search failed: {str(e)}")


@app.post("/chat")
async def chat(request: ChatRequest):
    """Chat endpoint - uses Duffel API for flight search"""

    # Simple parser for flight search intent
    msg = request.message.lower()

    # Extract origin (simple heuristic)
    origin = None
    date = None
    passengers = 1

    words = request.message.split()
    for i, word in enumerate(words):
        if word.lower() in ["from", "leaving"]:
            if i + 1 < len(words):
                origin = words[i + 1].strip(",")

    # If no structured data, return prompt
    if not origin:
        return {
            "thread_id": request.thread_id,
            "message": "Where are you flying from and when? (e.g., 'From Nashville on June 15')"
        }

    # Try to extract date (basic)
    import re
    date_match = re.search(r'\d{4}-\d{2}-\d{2}', request.message)
    if date_match:
        date = date_match.group()

    if not date:
        return {
            "thread_id": request.thread_id,
            "message": f"Got origin: {origin}. What's your departure date? (format: YYYY-MM-DD)"
        }

    # Extract passengers
    pass_match = re.search(r'(\d+)\s+passenger', request.message)
    if pass_match:
        passengers = int(pass_match.group(1))

    # Calculate return date (5 days default)
    dep_date = datetime.strptime(date, "%Y-%m-%d")
    ret_date = dep_date + timedelta(days=5)
    return_date = ret_date.strftime("%Y-%m-%d")

    # Search Duffel
    try:
        origin_code = parse_airport_code(origin)
        duffel = DuffelClient()
        result = await duffel.search_flights(
            origin=origin_code,
            destination="PDX",
            departure_date=date,
            return_date=return_date,
            passengers=passengers
        )

        formatted = format_duffel_for_display(result)

        return {
            "thread_id": request.thread_id,
            "message": formatted
        }

    except Exception as e:
        return {
            "thread_id": request.thread_id,
            "message": f"Search failed: {str(e)}"
        }
