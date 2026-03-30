"""
Google Flights script.ds:1 JS parser.
Parses all flight sections (Best flights + Other flights) and returns pure dicts.
No dependency on fast-flights model classes.
"""

from __future__ import annotations


def _fmt_time(time_list) -> str:
    if not time_list:
        return "00:00"
    try:
        h = int(time_list[0]) if time_list[0] is not None else 0
        m = int(time_list[1]) if len(time_list) > 1 and time_list[1] is not None else 0
        return f"{h:02d}:{m:02d}"
    except (TypeError, ValueError, IndexError):
        return "00:00"


def _fmt_date(date_list) -> str:
    if not date_list or len(date_list) < 3:
        return ""
    try:
        return f"{int(date_list[0])}-{int(date_list[1]):02d}-{int(date_list[2]):02d}"
    except (TypeError, ValueError):
        return ""


def parse_js(js: str) -> list[dict]:
    """
    Parse the Google Flights `script.ds:1` JS snippet.

    Iterates over ALL sections in data[3] (Best flights + Other flights),
    not just data[3][0], so low-traffic airlines (e.g. JX) are included.

    Returns a list of flight dicts:
    {
        "airlines": list[str],
        "price": "TWD 12345" or "",
        "stops": int,
        "segments": [
            {
                "from": "RMQ",
                "to": "KMJ",
                "departure": "2026-08-08 15:00",
                "arrival": "2026-08-08 18:15",
                "duration_min": 95,
                "plane": "Airbus A321neo",
            }
        ],
        "source": "gf_search",
    }
    """
    import rjsonc

    try:
        json_str = js.split("data:", 1)[1].rsplit(",", 1)[0]
        data = rjsonc.loads(json_str)
    except Exception:
        return []

    # data[3] may be None for small airports / special routes (Google uses lazy loading)
    if not isinstance(data[3], list):
        return []

    results: list[dict] = []

    for section in data[3]:           # iterate ALL sections, not just [0]
        if not isinstance(section, list):
            continue
        for k in section:
            try:
                flight    = k[0]
                price_raw = k[1][0][1]
                airlines  = flight[1] if isinstance(flight[1], list) else [flight[1]]

                segments: list[dict] = []
                for sf in flight[2]:
                    segments.append({
                        "from":         sf[3]  if len(sf) > 3  else "",
                        "to":           sf[6]  if len(sf) > 6  else "",
                        "departure":    f"{_fmt_date(sf[20] if len(sf) > 20 else None)} "
                                        f"{_fmt_time(sf[8]  if len(sf) > 8  else None)}".strip(),
                        "arrival":      f"{_fmt_date(sf[21] if len(sf) > 21 else None)} "
                                        f"{_fmt_time(sf[10] if len(sf) > 10 else None)}".strip(),
                        "duration_min": sf[11] if len(sf) > 11 else 0,
                        "plane":        sf[17] if len(sf) > 17 else "",
                    })

                results.append({
                    "airlines": airlines,
                    "price":    f"TWD {price_raw}" if price_raw else "",
                    "stops":    max(0, len(segments) - 1),
                    "segments": segments,
                    "source":   "gf_search",
                })
            except (IndexError, TypeError, KeyError):
                continue

    return results
