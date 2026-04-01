"""
Unit tests for gf_search.supplemental — schedule fallback.
"""

import json
import os
import tempfile
import pytest
from gf_search.supplemental import lookup, _operates_on
from datetime import date


# ── _operates_on ─────────────────────────────────────────────────────────────

class TestOperatesOn:

    def test_in_range_correct_weekday(self):
        periods = [{"from": "2026-08-01", "to": "2026-08-31", "weekdays": [0, 1, 2, 3, 4, 5, 6]}]
        assert _operates_on(date(2026, 8, 9), periods) is True  # Sunday

    def test_out_of_range(self):
        periods = [{"from": "2026-08-01", "to": "2026-08-08", "weekdays": [0,1,2,3,4,5,6]}]
        assert _operates_on(date(2026, 8, 9), periods) is False

    def test_wrong_weekday(self):
        # 2026-08-09 is Sunday (weekday=6); only Mon-Fri allowed
        periods = [{"from": "2026-08-01", "to": "2026-08-31", "weekdays": [0,1,2,3,4]}]
        assert _operates_on(date(2026, 8, 9), periods) is False

    def test_empty_periods(self):
        assert _operates_on(date(2026, 8, 9), []) is False

    def test_multiple_periods_first_match(self):
        periods = [
            {"from": "2026-07-01", "to": "2026-07-31", "weekdays": [0,1,2,3,4,5,6]},
            {"from": "2026-08-01", "to": "2026-08-31", "weekdays": [0,1,2,3,4,5,6]},
        ]
        assert _operates_on(date(2026, 8, 9), periods) is True

    def test_malformed_period_skipped(self):
        periods = [{"from": "not-a-date", "to": "2026-08-31", "weekdays": [0]}]
        assert _operates_on(date(2026, 8, 9), periods) is False


# ── lookup with synthetic schedules.json ─────────────────────────────────────

@pytest.fixture
def schedules_env(tmp_path, monkeypatch):
    """Provide a temp schedules.json via GF_SEARCH_SCHEDULES env var."""
    def _set(routes):
        path = tmp_path / "schedules.json"
        path.write_text(json.dumps({"routes": routes}), encoding="utf-8")
        monkeypatch.setenv("GF_SEARCH_SCHEDULES", str(path))
        return path
    return _set


_SAMPLE_ROUTE = {
    "origin": "RMQ",
    "destination": "KMJ",
    "airlines": ["JX"],
    "flight_no": "JX316",
    "dep_local": "15:00",
    "arr_local": "18:15",
    "duration_min": 95,
    "aircraft": "Airbus A321neo",
    "arr_day_offset": 0,
    "periods": [
        {"from": "2026-06-01", "to": "2026-10-31", "weekdays": [0,1,2,3,4,5,6]}
    ],
}


class TestLookup:

    def test_unknown_route_returns_empty(self):
        assert lookup("AAA", "BBB", "2026-08-09") == []

    def test_invalid_date_returns_empty(self):
        assert lookup("TPE", "NRT", "not-a-date") == []

    def test_matching_route(self, schedules_env):
        schedules_env([_SAMPLE_ROUTE])
        results = lookup("RMQ", "KMJ", "2026-08-09")
        assert len(results) == 1
        r = results[0]
        assert r["airlines"] == ["JX"]
        assert r["stops"] == 0
        assert r["source"] == "supplemental"
        assert r["price"] == ""  # supplemental has no live fares

    def test_case_insensitive_origin(self, schedules_env):
        schedules_env([_SAMPLE_ROUTE])
        assert lookup("rmq", "kmj", "2026-08-09") != []

    def test_segment_fields(self, schedules_env):
        schedules_env([_SAMPLE_ROUTE])
        r = lookup("RMQ", "KMJ", "2026-08-09")[0]
        s = r["segments"][0]
        assert s["from"] == "RMQ"
        assert s["to"] == "KMJ"
        assert s["flight_no"] == "JX316"
        assert "2026-08-09" in s["departure"]
        assert "15:00" in s["departure"]
        assert s["duration_min"] == 95
        assert s.get("plane") == "Airbus A321neo"  # schedules.json "aircraft" maps to segment "plane"

    def test_out_of_period_returns_empty(self, schedules_env):
        schedules_env([_SAMPLE_ROUTE])
        # 2025 is before the period start (2026-06-01)
        assert lookup("RMQ", "KMJ", "2025-08-09") == []

    def test_wrong_weekday_returns_empty(self, schedules_env):
        route = dict(_SAMPLE_ROUTE, periods=[
            {"from": "2026-06-01", "to": "2026-10-31", "weekdays": [0]}  # Monday only
        ])
        schedules_env([route])
        # 2026-08-09 is Sunday (weekday=6)
        assert lookup("RMQ", "KMJ", "2026-08-09") == []

    def test_arr_day_offset(self, schedules_env):
        route = dict(_SAMPLE_ROUTE, arr_day_offset=1)
        schedules_env([route])
        results = lookup("RMQ", "KMJ", "2026-08-09")
        s = results[0]["segments"][0]
        assert "2026-08-10" in s["arrival"]

    def test_multiple_matching_routes(self, schedules_env):
        route2 = dict(_SAMPLE_ROUTE, flight_no="JX318", dep_local="20:00")
        schedules_env([_SAMPLE_ROUTE, route2])
        results = lookup("RMQ", "KMJ", "2026-08-09")
        assert len(results) == 2
