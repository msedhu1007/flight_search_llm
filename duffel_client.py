"""Duffel API client for flight search"""
import os
from typing import Optional
import httpx
from datetime import datetime, timedelta


class DuffelClient:
    """Client for Duffel Flights API"""

    BASE_URL = "https://api.duffel.com"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("DUFFEL_API_KEY_LIVE")
        if not self.api_key:
            raise ValueError("DUFFEL_API_KEY_LIVE not set")

        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Duffel-Version": "v1"
        }

    async def search_flights(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: Optional[str] = None,
        passengers: int = 1,
        cabin_class: str = "economy",
        sort_by: str = "total_amount",
        max_budget_per_passenger: Optional[float] = None
    ) -> dict:
        """
        Search for flights using Duffel API

        Args:
            origin: Origin airport code (e.g., "BNA")
            destination: Destination airport code (e.g., "PDX")
            departure_date: Departure date in YYYY-MM-DD format
            return_date: Return date in YYYY-MM-DD format (None for one-way)
            passengers: Number of adult passengers (default: 1)
            cabin_class: Cabin class - economy, premium_economy, business, first
            sort_by: Sort preference - total_amount (cheapest), total_duration (fastest)
            max_budget_per_passenger: Filter flights above this price per passenger

        Returns:
            Dict with flight offers
        """

        # Build slices
        slices = [
            {
                "origin": origin.upper(),
                "destination": destination.upper(),
                "departure_date": departure_date
            }
        ]

        # Add return slice if round trip
        if return_date:
            slices.append({
                "origin": destination.upper(),
                "destination": origin.upper(),
                "departure_date": return_date
            })

        # Build passenger list
        passenger_list = [{"type": "adult"} for _ in range(passengers)]

        payload = {
            "data": {
                "slices": slices,
                "passengers": passenger_list,
                "cabin_class": cabin_class,
                "max_connections": 2
            }
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.BASE_URL}/air/offer_requests",
                headers=self.headers,
                json=payload
            )

            if response.status_code != 201:
                return {
                    "error": f"Duffel API error: {response.status_code}",
                    "message": response.text
                }

            data = response.json()

            # Extract offer request ID
            offer_request_id = data["data"]["id"]

            # Get offers with sort preference
            offers_response = await client.get(
                f"{self.BASE_URL}/air/offers",
                headers=self.headers,
                params={"offer_request_id": offer_request_id, "sort": sort_by}
            )

            if offers_response.status_code != 200:
                return {
                    "error": f"Failed to fetch offers: {offers_response.status_code}",
                    "message": offers_response.text
                }

            offers_data = offers_response.json()

            # Format and filter results
            return self._format_offers(offers_data, passengers, max_budget_per_passenger)

    def _format_offers(self, offers_data: dict, passengers: int, max_budget: Optional[float] = None) -> dict:
        """Format Duffel offers into readable structure"""

        offers = offers_data.get("data", [])

        if not offers:
            return {"message": "No flights found", "offers": []}

        # Filter by budget if specified
        if max_budget:
            filtered = []
            for offer in offers:
                total = float(offer.get("total_amount", "0"))
                per_passenger = total / passengers if passengers > 0 else total
                if per_passenger <= max_budget:
                    filtered.append(offer)
            offers = filtered

        if not offers:
            return {"message": f"No flights found within ${max_budget} per passenger budget", "offers": []}

        formatted = []

        for offer in offers[:5]:  # Top 5
            slices = offer.get("slices", [])

            # Get pricing
            total_amount = float(offer.get("total_amount", "0"))
            currency = offer.get("total_currency", "USD")
            per_passenger = total_amount / passengers if passengers > 0 else total_amount

            flight_info = {
                "offer_id": offer.get("id"),
                "total_price": f"{currency} {total_amount:.2f}",
                "price_per_passenger": f"{currency} {per_passenger:.2f}",
                "passengers": passengers,
                "slices": []
            }

            for slice_data in slices:
                segments = slice_data.get("segments", [])

                if not segments:
                    continue

                first_segment = segments[0]
                last_segment = segments[-1]

                slice_info = {
                    "origin": first_segment.get("origin", {}).get("iata_code"),
                    "destination": last_segment.get("destination", {}).get("iata_code"),
                    "departure_time": first_segment.get("departing_at"),
                    "arrival_time": last_segment.get("arriving_at"),
                    "duration": slice_data.get("duration"),
                    "stops": len(segments) - 1,
                    "segments": []
                }

                for seg in segments:
                    slice_info["segments"].append({
                        "airline": seg.get("marketing_carrier", {}).get("name"),
                        "flight_number": seg.get("marketing_carrier_flight_number"),
                        "origin": seg.get("origin", {}).get("iata_code"),
                        "destination": seg.get("destination", {}).get("iata_code"),
                        "departure": seg.get("departing_at"),
                        "arrival": seg.get("arriving_at"),
                        "duration": seg.get("duration")
                    })

                flight_info["slices"].append(slice_info)

            formatted.append(flight_info)

        return {
            "source": "Duffel",
            "offers": formatted,
            "total_offers": len(offers)
        }


def format_duffel_for_display(result: dict) -> str:
    """Format Duffel results for user display"""

    if "error" in result:
        return f"❌ Duffel search failed: {result.get('message', 'Unknown error')}"

    offers = result.get("offers", [])

    if not offers:
        return "No flights found from Duffel."

    output = ["🎯 **Flights from Duffel:**\n"]

    for i, offer in enumerate(offers[:3], 1):
        output.append(f"\n**Option {i}:**")
        output.append(f"💰 Total: {offer['total_price']} ({offer['price_per_passenger']} per person)")
        output.append(f"👥 Passengers: {offer['passengers']}")

        for j, slice_info in enumerate(offer["slices"], 1):
            slice_type = "Outbound" if j == 1 else "Return"
            output.append(f"\n**{slice_type} Flight:**")
            output.append(f"✈️ {slice_info['origin']} → {slice_info['destination']}")
            output.append(f"🕐 Departs: {slice_info['departure_time']}")
            output.append(f"🕑 Arrives: {slice_info['arrival_time']}")
            output.append(f"⏱️ Duration: {slice_info['duration']}")
            output.append(f"🔄 Stops: {slice_info['stops']}")

            if slice_info['stops'] > 0:
                output.append("Segments:")
                for seg in slice_info["segments"]:
                    output.append(f"  • {seg['airline']} {seg['flight_number']}: {seg['origin']} → {seg['destination']}")

        output.append("---")

    return "\n".join(output)
