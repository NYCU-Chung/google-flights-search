"""
Google Flights script.ds:1 JS parser.
Parses all flight sections (Best flights + Other flights) and returns pure dicts.
No dependency on fast-flights model classes.
"""

from __future__ import annotations

from ._utils import _fmt_date, _fmt_time


def parse_js(js: str) -> list[dict]:
    """
    Parse the Google Flights `script.ds:1` JS snippet.

    Iterates over data[2] (Best flights, e.g. Cathay Pacific) AND data[3]
    (Other flights), so all carriers including CX and low-traffic airlines
    like JX are included. Deduplicates entries that appear in both sections.

    Returns a list of flight dicts:
    {
        "airlines": list[str],     # IATA carrier code(s), e.g. ["JX"] or ["LJ", "KE"]
        "price": "TWD 12345" or "",
        "stops": int,
        "segments": [
            {
                "from": "RMQ",
                "to": "KMJ",
                "flight_no": "JX317",   # carrier+number from sf[22]; "" if unavailable
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

    # Collect sections from data[2] (Best flights) and data[3] (Other flights).
    # data[2] contains personalized "Best flights" (e.g. Cathay Pacific on TPE-NRT)
    # that are absent from data[3]. Both must be parsed to get full coverage.
    all_sections: list = []
    for idx in (2, 3):
        if idx < len(data) and isinstance(data[idx], list):
            all_sections.extend(data[idx])

    if not all_sections:
        return []

    results: list[dict] = []
    seen_keys: set = set()

    for section in all_sections:
        if not isinstance(section, list):
            continue
        for k in section:
            try:
                flight    = k[0]
                price_raw = k[1][0][1]
                try:
                    seg_carriers = [
                        sf[22][0]
                        for sf in flight[2]
                        if isinstance(sf, list) and len(sf) > 22
                        and isinstance(sf[22], list) and sf[22] and sf[22][0]
                    ]
                    airlines = list(dict.fromkeys(seg_carriers)) if seg_carriers else [flight[0]]
                except (IndexError, TypeError):
                    airlines = [flight[0]] if flight[0] else []

                segments: list[dict] = []
                for sf in flight[2]:
                    if not isinstance(sf, list):
                        continue
                    seg_info  = sf[22] if len(sf) > 22 and isinstance(sf[22], list) else []
                    flight_no = (
                        f"{seg_info[0]}{seg_info[1]}"
                        if len(seg_info) >= 2 and seg_info[0] and seg_info[1]
                        else ""
                    )
                    segments.append({
                        "from":         sf[3]  if len(sf) > 3  else "",
                        "to":           sf[6]  if len(sf) > 6  else "",
                        "flight_no":    flight_no,
                        "departure":    f"{_fmt_date(sf[20] if len(sf) > 20 else None)} "
                                        f"{_fmt_time(sf[8]  if len(sf) > 8  else None)}".strip(),
                        "arrival":      f"{_fmt_date(sf[21] if len(sf) > 21 else None)} "
                                        f"{_fmt_time(sf[10] if len(sf) > 10 else None)}".strip(),
                        "duration_min": sf[11] if len(sf) > 11 else 0,
                        "plane":        sf[17] if len(sf) > 17 else "",
                    })

                dedup_key = (
                    tuple(airlines),
                    segments[0].get("departure", "") if segments else "",
                )
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)

                entry: dict = {
                    "airlines": airlines,
                    "price":    f"TWD {price_raw}" if price_raw is not None else "",
                    "stops":    max(0, len(segments) - 1),
                    "segments": segments,
                    "source":   "gf_search",
                }
                # Include booking token for Stage-3 tfu construction; stripped before returning.
                try:
                    entry["_token"] = k[1][1]
                except (IndexError, TypeError):
                    pass
                results.append(entry)
            except (IndexError, TypeError, KeyError):
                continue

    return results
