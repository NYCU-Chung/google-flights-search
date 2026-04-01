"""
Unit tests for gf_search.parser — script.ds:1 JS parser.

All tests use synthetic data generated locally; no network required.
"""

import json
import pytest
from gf_search.parser import parse_js


# ── synthetic data helpers ────────────────────────────────────────────────────

def _make_segment(
    from_="RMQ", to="KMJ",
    carrier="JX", fno="316",
    dep_date=(2026, 8, 9), arr_date=(2026, 8, 9),
    dep_time=(15, 0), arr_time=(18, 15),
    dur=95, plane="Airbus A321neo",
):
    """
    Build a synthetic segment list (sf) matching the parser's field indices.
    sf must have at least 23 elements; most positions are None.
    """
    sf = [None] * 23
    sf[3]  = from_
    sf[6]  = to
    sf[8]  = list(dep_time)
    sf[10] = list(arr_time)
    sf[11] = dur
    sf[17] = plane
    sf[20] = list(dep_date)
    sf[21] = list(arr_date)
    sf[22] = [carrier, fno]
    return sf


def _make_flight(segments, price=10050, token="TOKEN_ABC"):
    """
    Build a synthetic flight entry: [flight_data, price_info].
    flight_data[0] = carrier code (display name; parser prefers sf[22][0])
    flight_data[2] = list of segments
    """
    carrier = segments[0][22][0] if segments and len(segments[0]) > 22 else "??"
    flight_data = [carrier, None, segments]
    price_info  = [[None, price], token]
    return [flight_data, price_info]


def _make_js(data2=None, data3=None):
    """
    Wrap sections into the script.ds:1 JS format the parser expects.
    data2 = list of sections for data[2] (Best flights)
    data3 = list of sections for data[3] (Other flights)
    """
    payload = [None, None, data2, data3]
    return f"data:{json.dumps(payload)},"


# ── basic parsing ─────────────────────────────────────────────────────────────

class TestParseBasic:

    def test_empty_string(self):
        assert parse_js("") == []

    def test_no_data_prefix(self):
        assert parse_js("garbage text") == []

    def test_null_data3(self):
        js = _make_js(data3=None)
        assert parse_js(js) == []

    def test_empty_sections(self):
        js = _make_js(data3=[[]])
        assert parse_js(js) == []

    def test_single_direct_flight(self):
        seg = _make_segment()
        flight = _make_flight([seg], price=10050)
        js = _make_js(data3=[[flight]])
        results = parse_js(js)
        assert len(results) == 1
        r = results[0]
        assert r["airlines"] == ["JX"]
        assert r["price"] == "TWD 10050"
        assert r["stops"] == 0
        assert r["source"] == "gf_search"

    def test_segment_fields(self):
        seg = _make_segment(
            from_="RMQ", to="KMJ",
            carrier="JX", fno="316",
            dep_date=(2026, 8, 9), arr_date=(2026, 8, 9),
            dep_time=(15, 0), arr_time=(18, 15),
            dur=95, plane="Airbus A321neo",
        )
        flight = _make_flight([seg])
        js = _make_js(data3=[[flight]])
        results = parse_js(js)
        s = results[0]["segments"][0]
        assert s["from"] == "RMQ"
        assert s["to"] == "KMJ"
        assert s["flight_no"] == "JX316"
        assert s["departure"] == "2026-08-09 15:00"
        assert s["arrival"] == "2026-08-09 18:15"
        assert s["duration_min"] == 95
        assert s["plane"] == "Airbus A321neo"

    def test_token_extracted(self):
        seg = _make_segment()
        flight = _make_flight([seg], token="MY_TOKEN_XYZ")
        js = _make_js(data3=[[flight]])
        results = parse_js(js)
        assert results[0].get("_token") == "MY_TOKEN_XYZ"

    def test_zero_price(self):
        """Price = 0 should produce 'TWD 0' not ''."""
        seg = _make_segment()
        flight = _make_flight([seg], price=0)
        js = _make_js(data3=[[flight]])
        results = parse_js(js)
        assert results[0]["price"] == "TWD 0"

    def test_none_price(self):
        """price_raw = None should produce ''."""
        seg = _make_segment()
        flight_data = ["JX", None, [seg]]
        price_info  = [[None, None], "TOKEN"]
        flight = [flight_data, price_info]
        js = _make_js(data3=[[flight]])
        results = parse_js(js)
        assert results[0]["price"] == ""


# ── connecting flights ────────────────────────────────────────────────────────

class TestConnectingFlights:

    def test_two_segment_flight(self):
        seg1 = _make_segment(from_="RMQ", to="ICN", carrier="TW", fno="670",
                             dep_date=(2026,8,9), arr_date=(2026,8,9),
                             dep_time=(17,0), arr_time=(21,30), dur=150)
        seg2 = _make_segment(from_="ICN", to="KMJ", carrier="TW", fno="287",
                             dep_date=(2026,8,10), arr_date=(2026,8,10),
                             dep_time=(7,55), arr_time=(10,5), dur=70)
        flight = _make_flight([seg1, seg2], price=7563)
        js = _make_js(data3=[[flight]])
        results = parse_js(js)
        assert len(results) == 1
        r = results[0]
        assert r["stops"] == 1
        assert r["airlines"] == ["TW"]
        assert len(r["segments"]) == 2
        assert r["segments"][0]["flight_no"] == "TW670"
        assert r["segments"][1]["flight_no"] == "TW287"

    def test_mixed_carriers(self):
        seg1 = _make_segment(from_="TPE", to="HKG", carrier="CX", fno="465")
        seg2 = _make_segment(from_="HKG", to="LHR", carrier="CX", fno="235")
        flight = _make_flight([seg1, seg2])
        js = _make_js(data3=[[flight]])
        results = parse_js(js)
        assert results[0]["airlines"] == ["CX"]

    def test_different_carriers_per_segment(self):
        seg1 = _make_segment(carrier="JL", fno="808")
        seg2 = _make_segment(carrier="JL", fno="629")
        seg3 = _make_segment(carrier="JX", fno="312")
        flight = _make_flight([seg3, seg1, seg2])
        js = _make_js(data3=[[flight]])
        results = parse_js(js)
        # Airlines deduplicated in order of appearance
        assert "JX" in results[0]["airlines"]
        assert "JL" in results[0]["airlines"]


# ── data[2] Best Flights ──────────────────────────────────────────────────────

class TestData2BestFlights:

    def test_data2_parsed(self):
        """Flights in data[2] (Best flights) must be included."""
        seg = _make_segment(carrier="CX", fno="465")
        flight = _make_flight([seg], price=25000)
        js = _make_js(data2=[[flight]], data3=None)
        results = parse_js(js)
        assert len(results) == 1
        assert results[0]["airlines"] == ["CX"]

    def test_both_sections_merged(self):
        """data[2] and data[3] results are merged, no duplicates."""
        seg_cx = _make_segment(carrier="CX", fno="465", dep_time=(9, 0))
        seg_jx = _make_segment(carrier="JX", fno="316", dep_time=(15, 0))
        flight_cx = _make_flight([seg_cx], price=25000)
        flight_jx = _make_flight([seg_jx], price=10050)
        js = _make_js(data2=[[flight_cx]], data3=[[flight_jx]])
        results = parse_js(js)
        assert len(results) == 2
        airlines = {r["airlines"][0] for r in results}
        assert "CX" in airlines
        assert "JX" in airlines

    def test_deduplication_same_flight_both_sections(self):
        """Same flight in data[2] and data[3] should appear only once."""
        seg = _make_segment(carrier="JX", fno="316", dep_time=(15, 0))
        flight = _make_flight([seg], price=10050)
        js = _make_js(data2=[[flight]], data3=[[flight]])
        results = parse_js(js)
        assert len(results) == 1


# ── multiple flights ──────────────────────────────────────────────────────────

class TestMultipleFlights:

    def test_multiple_flights_in_section(self):
        f1 = _make_flight([_make_segment(dep_time=(8, 0))], price=8000)
        f2 = _make_flight([_make_segment(dep_time=(15, 0))], price=10050)
        f3 = _make_flight([_make_segment(dep_time=(20, 0))], price=12000)
        js = _make_js(data3=[[f1, f2, f3]])
        results = parse_js(js)
        assert len(results) == 3

    def test_multiple_sections(self):
        f1 = _make_flight([_make_segment(dep_time=(8, 0))], price=8000)
        f2 = _make_flight([_make_segment(dep_time=(15, 0))], price=10050)
        # Two separate sections inside data[3]
        js = _make_js(data3=[[f1], [f2]])
        results = parse_js(js)
        assert len(results) == 2


# ── edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_missing_flight_no(self):
        """sf[22] too short → flight_no should be ''."""
        sf = [None] * 23
        sf[3] = "TPE"; sf[6] = "NRT"
        sf[8] = [10, 0]; sf[10] = [14, 0]
        sf[11] = 180; sf[17] = ""
        sf[20] = [2026, 8, 8]; sf[21] = [2026, 8, 8]
        sf[22] = []   # empty — no carrier or number
        flight_data = ["?", None, [sf]]
        price_info  = [[None, 5000], "T"]
        flight = [flight_data, price_info]
        js = _make_js(data3=[[flight]])
        results = parse_js(js)
        assert results[0]["segments"][0]["flight_no"] == ""

    def test_invalid_price_entry_skipped(self):
        """Flight with entirely missing price_info should be skipped gracefully."""
        seg = _make_segment()
        flight_data = ["JX", None, [seg]]
        flight = [flight_data]   # no price_info at index 1
        js = _make_js(data3=[[flight]])
        # Should not raise; just return empty or skip
        results = parse_js(js)
        assert isinstance(results, list)

    def test_timestamp_midnight(self):
        seg = _make_segment(dep_time=(0, 0), arr_time=(0, 0))
        flight = _make_flight([seg])
        js = _make_js(data3=[[flight]])
        results = parse_js(js)
        assert results[0]["segments"][0]["departure"].endswith("00:00")

    def test_result_schema_keys(self):
        """Every result must have the documented keys."""
        seg = _make_segment()
        flight = _make_flight([seg])
        js = _make_js(data3=[[flight]])
        results = parse_js(js)
        r = results[0]
        for key in ("airlines", "price", "stops", "segments", "source"):
            assert key in r, f"Missing key: {key}"
        s = r["segments"][0]
        for key in ("from", "to", "flight_no", "departure", "arrival", "duration_min", "plane"):
            assert key in s, f"Missing segment key: {key}"
