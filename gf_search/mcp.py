"""
gf_search MCP Server

Exposes Google Flights search as MCP tools for Claude and other AI assistants.

Claude Desktop config (uvx — no pre-installation needed):
    {
      "mcpServers": {
        "google-flights": {
          "command": "uvx",
          "args": ["--from", "gf-search", "gf-search-mcp"]
        }
      }
    }

Claude Desktop config (after pip install gf-search):
    {
      "mcpServers": {
        "google-flights": {
          "command": "gf-search-mcp"
        }
      }
    }
"""

import asyncio
from datetime import date as _date, timedelta as _timedelta

from mcp.server.fastmcp import FastMCP
from .search import search as _search_sync
from .multi_city import search_multi_city as _search_multi_city_sync

mcp = FastMCP("Google Flights Search")


@mcp.tool()
async def search_flights(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None = None,
    adults: int = 1,
    travel_class: str = "economy",
    max_results: int = 5,
    max_stops: int | None = None,
) -> list[dict]:
    """
    Search Google Flights for available flights.

    Args:
        origin: IATA departure airport code (e.g. "TPE", "RMQ")
        destination: IATA arrival airport code (e.g. "NRT", "KMJ")
        departure_date: Date string "YYYY-MM-DD"
        return_date: Return date "YYYY-MM-DD" for round-trip; None for one-way
        adults: Number of adult passengers
        travel_class: One of "economy", "premium-economy", "business", "first"
        max_results: Maximum number of results to return
        max_stops: Maximum number of stops (0=direct only, 1=max 1 stop, None=any)

    Returns:
        List of flights, each with airlines, price, stops, and segments detail.
        For round-trip small-airport fallback, results include "direction" field
        ("outbound" or "inbound").
    """
    results = await asyncio.to_thread(
        _search_sync,
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        adults=adults,
        travel_class=travel_class,
        max_results=max_results,
        max_stops=max_stops,
    )

    # Add hints when results are incomplete
    if not results:
        from .setup import session_status
        status = session_status()
        hint = (
            f"No flights found for {origin}→{destination} on {departure_date}. "
            "This may be a niche route that requires Google's on-demand computation. "
        )
        if not status["valid"]:
            hint += "Run gf_search.setup() to enable full results for niche routes."
        return [{"_hint": hint, "flights": []}]

    all_priceless = all(not f.get("price") for f in results)
    if all_priceless:
        from .setup import session_status
        status = session_status()
        for f in results:
            f["_note"] = "Flight found but price unavailable via API."
            if not status["valid"]:
                f["_note"] += " Run gf_search.setup() to improve price coverage."

    return results


@mcp.tool()
async def search_multi_city_flights(
    segments: list[dict],
    adults: int = 1,
    travel_class: str = "economy",
    max_results: int = 5,
) -> list[dict]:
    """
    Search multi-city itineraries on Google Flights (open-jaw, 4-leg, etc.).

    Returns both combined itineraries (single PNR, booking_token present) and
    per-leg aggregated results for comparison.

    Args:
        segments: List of legs, each a dict with keys "from", "to", "date".
                  Example: [
                      {"from": "TPE", "to": "NRT", "date": "2026-05-01"},
                      {"from": "NRT", "to": "LHR", "date": "2026-05-03"},
                      {"from": "LHR", "to": "TPE", "date": "2026-05-10"},
                  ]
                  Minimum 2 legs, maximum 5 legs.
        adults: Number of adult passengers
        travel_class: One of "economy", "premium-economy", "business", "first"
        max_results: Maximum number of results per source type

    Returns:
        List of itineraries. Results with "booking_token" are combined tickets
        bookable directly on Google Flights.
    """
    for seg in segments:
        missing = [k for k in ("from", "to", "date") if k not in seg]
        if missing:
            raise ValueError(f"Segment {seg!r} missing required keys: {missing}")

    return await asyncio.to_thread(
        _search_multi_city_sync,
        segments=segments,
        adults=adults,
        travel_class=travel_class,
        max_results=max_results,
    )


@mcp.tool()
async def generate_search_urls(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None = None,
    adults: int = 1,
    travel_class: str = "economy",
) -> dict:
    """
    Generate a Google Flights search URL that can be opened in a browser.

    Use this when search_flights returns empty/priceless results for niche routes.
    Opening the URL in a real browser triggers Google's on-demand computation and
    shows complete results with prices.

    Args:
        origin: IATA departure airport code
        destination: IATA arrival airport code
        departure_date: Date string "YYYY-MM-DD"
        return_date: Return date "YYYY-MM-DD" for round-trip; None for one-way
        adults: Number of adult passengers
        travel_class: One of "economy", "premium-economy", "business", "first"

    Returns:
        Dict with 'url' (Google Flights URL) and 'description'.
    """
    from .builder import build_tfs

    _SEAT_MAP = {"economy": 1, "premium-economy": 2, "premium economy": 2, "business": 3, "first": 4}
    seat = _SEAT_MAP.get(travel_class.lower(), 1)
    tfs = build_tfs(origin.upper(), destination.upper(), departure_date, return_date, seat=seat, adults=adults)
    url = f"https://www.google.com/travel/flights/search?tfs={tfs}&tfu=EgIIACIA&hl=zh-TW"

    desc = f"{origin}→{destination} {departure_date}"
    if return_date:
        desc += f" / {return_date}"
    desc += f" ({travel_class}, {adults}人)"

    return {"url": url, "description": desc}


@mcp.tool()
async def search_cheapest_dates(
    origin: str,
    destination: str,
    date_from: str,
    date_to: str,
    return_trip_days: int | None = None,
    adults: int = 1,
    travel_class: str = "economy",
    max_stops: int | None = None,
) -> list[dict]:
    """
    Find the cheapest departure dates within a date range.

    Searches each date in the range and returns results sorted by price,
    useful for flexible-date travellers looking for the best deal.

    Args:
        origin: IATA departure airport code
        destination: IATA arrival airport code
        date_from: Start of date range "YYYY-MM-DD"
        date_to: End of date range "YYYY-MM-DD"
        return_trip_days: If set, search round-trip with return N days after departure.
                          If None, search one-way.
        adults: Number of adult passengers
        travel_class: One of "economy", "premium-economy", "business", "first"
        max_stops: Maximum number of stops (0=direct only, None=any)

    Returns:
        List of {date, cheapest_price, flights: [...]} sorted by cheapest_price.
    """
    start = _date.fromisoformat(date_from)
    end = _date.fromisoformat(date_to)
    if end < start:
        raise ValueError(f"date_to ({date_to}) must be >= date_from ({date_from})")
    if (end - start).days > 30:
        raise ValueError("Date range must be <= 30 days to avoid excessive requests")

    results: list[dict] = []
    current = start
    while current <= end:
        dep = current.isoformat()
        ret = (current + _timedelta(days=return_trip_days)).isoformat() if return_trip_days else None
        flights = await asyncio.to_thread(
            _search_sync,
            origin=origin,
            destination=destination,
            departure_date=dep,
            return_date=ret,
            adults=adults,
            travel_class=travel_class,
            max_results=3,
            max_stops=max_stops,
        )
        if flights:
            cheapest = min(
                (f for f in flights if f.get("price")),
                key=lambda f: int(f["price"].split()[-1]) if f["price"] else 10**9,
                default=None,
            )
            results.append({
                "date": dep,
                "cheapest_price": cheapest["price"] if cheapest else "",
                "flights": flights,
            })
        current += _timedelta(days=1)

    results.sort(key=lambda r: int(r["cheapest_price"].split()[-1]) if r["cheapest_price"] else 10**9)
    return results


def main():
    mcp.run()


if __name__ == "__main__":
    main()
