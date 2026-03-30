"""
Google Flights SSR fetcher using primp (no Playwright, no Google session needed).
"""

from __future__ import annotations

_SEAT_MAP: dict[str, int] = {
    "economy": 1,
    "premium-economy": 2,
    "premium economy": 2,
    "business": 3,
    "first": 4,
}

_GF_SEARCH_URL = "https://www.google.com/travel/flights/search"


def fetch(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None = None,
    seat: str = "economy",
    adults: int = 1,
    max_results: int = 5,
) -> list[dict]:
    """
    Fetch Google Flights results via primp SSR (impersonates Chrome, no browser needed).

    Returns a list of flight dicts as defined in parser.parse_js().
    Returns [] if primp/selectolax is unavailable or if the response contains no data.
    """
    try:
        from primp import Client
        from selectolax.lexbor import LexborHTMLParser
    except ImportError:
        return []

    from .builder import build_tfs
    from .parser import parse_js

    seat_no = _SEAT_MAP.get(seat.lower(), 1)

    # For one-way queries, synthesise a return date so that Google performs on-demand
    # calculation for small/low-traffic airports (data[3] may be null otherwise).
    from datetime import datetime as _dt, timedelta as _td
    synth_return = return_date or (
        _dt.strptime(departure_date, "%Y-%m-%d") + _td(days=7)
    ).strftime("%Y-%m-%d")

    tfs = build_tfs(
        origin.upper(), destination.upper(),
        departure_date, synth_return,
        seat=seat_no, adults=adults,
    )

    import time as _time

    params = {"tfs": tfs, "tfu": "EgIIACIA", "hl": "zh-TW"}
    headers = {"Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"}

    # Google SSR is non-deterministic: data[3] may be null on first hit but
    # populated on retry (edge cache warm-up). Retry up to 3 times total.
    for attempt in range(3):
        if attempt > 0:
            _time.sleep(1.5)
        try:
            client = _make_client()
            res = client.get(_GF_SEARCH_URL, params=params, headers=headers)
        except Exception:
            continue

        try:
            html_parser = LexborHTMLParser(res.text)
            ds1 = html_parser.css_first(r"script.ds\:1")
            if not ds1:
                continue
            txt = ds1.text()
            if "data:" not in txt:
                continue
        except Exception:
            continue

        results = parse_js(txt)
        if results:
            return results[:max_results]

    return []


def _make_client():
    """Create a primp Client with Chrome impersonation, falling back to random."""
    from primp import Client
    try:
        return Client(
            impersonate="chrome_133",
            impersonate_os="macos",
            referer=True,
            cookie_store=True,
        )
    except Exception:
        return Client(
            impersonate="random",
            referer=True,
            cookie_store=True,
        )
