"""
Direct airline booking API adapters.

Each adapter is a callable with signature:
    fn(client, origin, destination, departure_date, seat_no, adults)
    -> Iterable[dict]   # gf_search-format flight dicts

Adapters are registered via ``register()``.
``iter_direct_flights()`` tries every registered adapter and yields results.

Adding a new airline: implement one function, call register() — fetcher.py
needs no changes.
"""

from __future__ import annotations

_REGISTRY: list[tuple[str, object]] = []


def register(name: str, fn) -> None:
    _REGISTRY.append((name, fn))


def iter_direct_flights(client, origin: str, destination: str,
                        departure_date: str, seat_no: int, adults: int):
    """Yield gf_search-format dicts from every registered airline adapter."""
    for _name, fn in _REGISTRY:
        try:
            yield from fn(client, origin, destination, departure_date, seat_no, adults)
        except Exception:
            pass


# ── JX Starlux ────────────────────────────────────────────────────────────────

def _jx_search(client, origin: str, destination: str,
               departure_date: str, seat_no: int, adults: int):
    """
    Starlux Airlines direct booking API.
    Endpoint: https://ecapi.starlux-airlines.com/searchFlight/v2/flights/search
    No authentication required.
    """
    import json as _json

    _cabin_map = {1: "eco", 2: "ecoPremium", 3: "business", 4: "first"}
    cabin = _cabin_map.get(seat_no, "eco")

    resp = client.post(
        "https://ecapi.starlux-airlines.com/searchFlight/v2/flights/search",
        json={
            "cabin": cabin,
            "itineraries": [{"departure": origin, "arrival": destination,
                              "departureDate": departure_date}],
            "travelers": {"adt": adults, "chd": 0, "inf": 0},
        },
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "jx-lang": "en-us",
            "Referer": "https://www.starlux-airlines.com/",
            "Origin": "https://www.starlux-airlines.com",
        },
    )
    data = _json.loads(resp.text)
    if not (data.get("success") and data.get("data")):
        return

    for fl in data["data"].get("flights", []):
        price_amt = None
        for pi in fl.get("priceInfo", []):
            if pi.get("cabin") == cabin:
                price_amt = pi.get("from", {}).get("amount")
                break
        if price_amt is None:
            for pi in fl.get("priceInfo", []):
                amt = pi.get("from", {}).get("amount")
                if amt and (price_amt is None or amt < price_amt):
                    price_amt = amt
        if price_amt is None:
            continue

        segs = []
        for seg in fl.get("flightDetails", []):
            dep_dt = seg["departure"]["dateTime"]
            arr_dt = seg["arrival"]["dateTime"]
            flight_no = seg["marketingAirlineCode"] + seg["marketingFlightNumber"]
            segs.append({
                "from": seg["departure"]["airport"],
                "to": seg["arrival"]["airport"],
                "flight_no": flight_no,
                "departure": dep_dt[:10] + " " + dep_dt[11:16],
                "arrival": arr_dt[:10] + " " + arr_dt[11:16],
                "duration_min": seg.get("duration", 0),
                "plane": seg.get("aircraftCode", ""),
            })
        if not segs:
            continue

        airlines = list(dict.fromkeys(s["flight_no"][:2] for s in segs))
        stops = 0 if fl.get("isDirect") else len(segs) - 1
        yield {
            "airlines": airlines,
            "price": f"TWD {price_amt}",
            "stops": stops,
            "segments": segs,
            "source": "gf_search",
        }


# Airline-specific adapters can be registered here.
# Currently empty: the Playwright-based Stage 5 in fetcher.py provides
# general Google Flights coverage with a real Chrome session.
# register("XX", _xx_search)
