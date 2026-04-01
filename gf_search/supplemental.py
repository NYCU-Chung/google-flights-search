"""
Generic supplemental schedule fallback for routes not yet indexed by Google Flights SSR.

Schedule data lives in a JSON file — no airline-specific code here.

File resolution order (first found wins):
  1. $GF_SEARCH_SCHEDULES environment variable (absolute path)
  2. ~/.gf_search/schedules.json          (user overrides / additions)
  3. <package_dir>/schedules.json         (bundled defaults)

To add a new airline / route: edit the JSON file, no code changes required.
See gf_search/schedules.json for the schema.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any


def _load_schedules() -> list[dict]:
    """Load route records from the first schedules.json that exists."""
    candidates: list[Path] = []

    env = os.environ.get("GF_SEARCH_SCHEDULES")
    if env:
        candidates.append(Path(env))

    candidates.append(Path.home() / ".gf_search" / "schedules.json")
    candidates.append(Path(__file__).parent / "schedules.json")

    for path in candidates:
        if path.is_file():
            try:
                with path.open(encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("routes", [])
            except Exception:
                continue

    return []


def _operates_on(query_date: date, periods: list[dict[str, Any]]) -> bool:
    wd = query_date.weekday()
    for p in periods:
        try:
            start = date.fromisoformat(p["from"])
            end   = date.fromisoformat(p["to"])
            if start <= query_date <= end and wd in p["weekdays"]:
                return True
        except (KeyError, ValueError):
            continue
    return False


def lookup(origin: str, destination: str, departure_date: str) -> list[dict]:
    """
    Return supplemental flight dicts for routes absent from Google Flights.

    Results match the parser.parse_js() schema with source="supplemental".
    Price is always "" (static schedules have no live fare data).
    Returns [] if no matching route / date found.
    """
    try:
        dep_date = date.fromisoformat(departure_date)
    except ValueError:
        return []

    org = origin.upper()
    dst = destination.upper()

    results: list[dict] = []
    for route in _load_schedules():
        if route.get("origin", "").upper() != org:
            continue
        if route.get("destination", "").upper() != dst:
            continue
        if not _operates_on(dep_date, route.get("periods", [])):
            continue

        offset   = int(route.get("arr_day_offset", 0))
        arr_date = dep_date + timedelta(days=offset)

        segment = {
            "from":         org,
            "to":           dst,
            "flight_no":    route.get("flight_no", ""),
            "departure":    f"{dep_date.isoformat()} {route.get('dep_local', '')}",
            "arrival":      f"{arr_date.isoformat()} {route.get('arr_local', '')}",
            "duration_min": int(route.get("duration_min", 0)),
            "plane":        route.get("aircraft", ""),
        }

        results.append({
            "airlines": list(route.get("airlines", [])),
            "price":    "",
            "stops":    0,
            "segments": [segment],
            "source":   "supplemental",
        })

    return results
