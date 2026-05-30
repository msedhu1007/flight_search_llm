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
  You are a friendly flight-search assistant. Help users find the cheapest flights by asking
  for required details conversationally.

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
      origin: str = Field(..., description="Origin city or airport, for example BNA or
  Nashville")
      departure_date: str = Field(..., description="Departure date, for example 2026-03-31")
      passengers: int = Field(default=1, ge=1, description="Number of passengers")
      destination: str = Field(default="Portland, Oregon, PDX")
      return_date: Optional[str] = Field(default=None, description="Return date, for example
  2026-04-05")
      trip_length_days: int = Field(default=5, ge=1, description="Used if return_date is not
  provided")
      sort_by: str = Field(default="total_amount", description="Sort by: total_amount
  (cheapest) or total_duration (fastest)")
      max_budget_per_passenger: Optional[float] = Field(default=None, description="Max budget
  per passenger in USD")


  class ChatRequest(BaseModel):
      message: str = Field(..., description="User's message")
      thread_id: Optional[str] = Field(default="default", description="Conversation thread ID
  for session tracking")




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

      # Parse origin to airport code
      origin_code = request.origin.upper().split()[-1] if len(request.origin) <= 4 else
  request.origin[:3].upper()
      dest_code = "PDX"  # Portland

      try:
          duffel = DuffelClient()
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
              "message": "Where are you flying from and when? (e.g., 'From Nashville on June
  15')"
          }

      # Try to extract date (basic)
      import re
      date_match = re.search(r'\d{4}-\d{2}-\d{2}', request.message)
      if date_match:
          date = date_match.group()

      if not date:
          return {
              "thread_id": request.thread_id,
              "message": f"Got origin: {origin}. What's your departure date? (format:
  YYYY-MM-DD)"
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
          origin_code = origin.upper() if len(origin) <= 3 else origin[:3].upper()
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
