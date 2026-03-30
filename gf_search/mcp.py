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

from mcp.server.fastmcp import FastMCP
from .search import search
from .multi_city import search_multi_city

mcp = FastMCP("Google Flights Search")


@mcp.tool()
def search_flights(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None = None,
    adults: int = 1,
    travel_class: str = "economy",
    max_results: int = 5,
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

    Returns:
        List of flights, each with airlines, price, stops, and segments detail.
    """
    return search(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        adults=adults,
        travel_class=travel_class,
        max_results=max_results,
    )


@mcp.tool()
def search_multi_city_flights(
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
    return search_multi_city(
        segments=segments,
        adults=adults,
        travel_class=travel_class,
        max_results=max_results,
    )


def main():
    mcp.run()


if __name__ == "__main__":
    main()
