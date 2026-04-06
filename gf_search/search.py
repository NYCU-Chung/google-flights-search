"""
Main entry point for gf_search package.
"""

from __future__ import annotations

_TRAVEL_CLASS_TO_SEAT: dict[str, str] = {
    "economy":          "economy",
    "premium-economy":  "premium-economy",
    "premium economy":  "premium-economy",
    "business":         "business",
    "first":            "first",
}


def search(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None = None,
    adults: int = 1,
    travel_class: str = "economy",  # economy / premium-economy / business / first
    max_results: int = 5,
    currency: str = "TWD",
    max_stops: int | None = None,
) -> list[dict]:
    """
    Search Google Flights via SSR (no Playwright, no session required).

    Parameters
    ----------
    origin : str
        IATA code of departure airport (e.g. "TPE", "RMQ").
    destination : str
        IATA code of arrival airport.
    departure_date : str
        Date string "YYYY-MM-DD".
    return_date : str | None
        Return date "YYYY-MM-DD" for round trips, None for one-way.
    adults : int
        Number of adult passengers.
    travel_class : str
        One of: economy, premium-economy, business, first.
    max_results : int
        Maximum number of results to return.

    Returns
    -------
    list[dict]
        Each dict has the shape:
        {
            "airlines": list[str],
            "price": "TWD 12345",   # or ""
            "stops": int,
            "segments": [
                {
                    "from": str,
                    "to": str,
                    "departure": "YYYY-MM-DD HH:MM",
                    "arrival": "YYYY-MM-DD HH:MM",
                    "duration_min": int,
                    "plane": str,
                }
            ],
            "source": "gf_search",   # or "jx_static" for routes not yet in Google
        }
    """
    from .fetcher import fetch

    seat = _TRAVEL_CLASS_TO_SEAT.get(travel_class.lower(), "economy")
    return fetch(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        seat=seat,
        adults=adults,
        max_results=max_results,
        currency=currency,
        max_stops=max_stops,
    )
