"""
Unit tests for gf_search.multi_city — multi-city flight search parsing.

All tests use synthetic data; no network required.
"""

import json
import pytest
from gf_search.multi_city import _parse_batch_response, _parse_flight_section


# ── synthetic data helpers ────────────────────────────────────────────────────

def _make_segment_raw(
    from_="TPE", to="NRT",
    carrier="CI", fno="107",
    dep_time=(10, 0), arr_time=(14, 0),
    dep_date=(2026, 5, 1), arr_date=(2026, 5, 1),
    dur=180, plane="Boeing 777-300ER",
):
    """Build a synthetic raw segment list matching multi_city parser field indices."""
    sf = [None] * 23
    sf[3] = from_
    sf[6] = to
    sf[8] = list(dep_time)
    sf[10] = list(arr_time)
    sf[11] = dur
    sf[17] = plane
    sf[20] = list(dep_date)
    sf[21] = list(arr_date)
    sf[22] = [carrier, fno]
    return sf


def _make_item(segments_raw, price=15000, token="TOKEN_123"):
    """Build a synthetic flight item: [flight_data, price_info]."""
    carrier = segments_raw[0][22][0] if segments_raw and len(segments_raw[0]) > 22 else "??"
    flight_data = [carrier, None, segments_raw]
    price_info = [[None, price], token]
    return [flight_data, price_info]


def _make_batch_response(items, sec_idx=2):
    """Wrap items into a batchexecute-style response string."""
    cd = [None, None, None, None]
    if sec_idx == 2:
        cd[2] = [items]
    elif sec_idx == 3:
        cd[3] = [items]
    entry = ["wrb.fr", "LqxFAb", json.dumps(cd), None, None, None, "generic"]
    outer = [entry]
    return ")]}'\n" + json.dumps(outer)


# ── _parse_flight_section ────────────────────────────────────────────────────

class TestParseFlightSection:

    def test_single_direct_flight(self):
        seg = _make_segment_raw()
        item = _make_item([seg], price=15000)
        opts = _parse_flight_section([item])
        assert len(opts) == 1
        assert opts[0]["price"] == 15000
        assert opts[0]["airlines"] == ["CI"]
        assert opts[0]["stops"] == 0
        assert len(opts[0]["segments"]) == 1

    def test_connecting_flight(self):
        seg1 = _make_segment_raw(from_="TPE", to="HKG", carrier="CX", fno="465")
        seg2 = _make_segment_raw(from_="HKG", to="LHR", carrier="CX", fno="251")
        item = _make_item([seg1, seg2], price=45000)
        opts = _parse_flight_section([item])
        assert len(opts) == 1
        assert opts[0]["stops"] == 1
        assert opts[0]["airlines"] == ["CX"]
        assert len(opts[0]["segments"]) == 2

    def test_token_preserved(self):
        seg = _make_segment_raw()
        item = _make_item([seg], token="MY_TOKEN")
        opts = _parse_flight_section([item])
        assert opts[0]["token"] == "MY_TOKEN"

    def test_segment_fields(self):
        seg = _make_segment_raw(
            from_="NRT", to="LHR", carrier="JL", fno="43",
            dep_time=(11, 30), arr_time=(15, 45),
            dep_date=(2026, 5, 3), arr_date=(2026, 5, 3),
            dur=720, plane="Boeing 787-9",
        )
        item = _make_item([seg])
        opts = _parse_flight_section([item])
        s = opts[0]["segments"][0]
        assert s["from"] == "NRT"
        assert s["to"] == "LHR"
        assert s["flight_no"] == "JL43"
        assert "2026-05-03" in s["departure"]
        assert "11:30" in s["departure"]
        assert s["duration_min"] == 720
        assert s["plane"] == "Boeing 787-9"

    def test_empty_section(self):
        assert _parse_flight_section([]) == []

    def test_malformed_item_skipped(self):
        good = _make_item([_make_segment_raw()], price=10000)
        bad = ["not", "a", "valid"]
        opts = _parse_flight_section([bad, good])
        assert len(opts) == 1
        assert opts[0]["price"] == 10000

    def test_multiple_items(self):
        item1 = _make_item([_make_segment_raw(dep_time=(8, 0))], price=12000)
        item2 = _make_item([_make_segment_raw(dep_time=(14, 0))], price=18000)
        opts = _parse_flight_section([item1, item2])
        assert len(opts) == 2


# ── _parse_batch_response ────────────────────────────────────────────────────

class TestParseBatchResponse:

    def test_valid_response(self):
        seg = _make_segment_raw()
        item = _make_item([seg], price=20000)
        raw = _make_batch_response([item])
        opts = _parse_batch_response(raw)
        assert len(opts) == 1
        assert opts[0]["price"] == 20000

    def test_empty_string(self):
        assert _parse_batch_response("") == []

    def test_no_prefix(self):
        assert _parse_batch_response("invalid data") == []

    def test_invalid_json(self):
        assert _parse_batch_response(")]}'\\n{broken") == []

    def test_no_wrb_entry(self):
        raw = ")]}'\n" + json.dumps([["not_wrb", "x", None]])
        assert _parse_batch_response(raw) == []

    def test_data3_section(self):
        seg = _make_segment_raw(carrier="BR", fno="108")
        item = _make_item([seg], price=16000)
        raw = _make_batch_response([item], sec_idx=3)
        opts = _parse_batch_response(raw)
        assert len(opts) == 1
        assert opts[0]["airlines"] == ["BR"]

    def test_chunk_length_prefix_stripped(self):
        seg = _make_segment_raw()
        item = _make_item([seg], price=25000)
        cd = [None, None, [[item]], None]
        entry = ["wrb.fr", "LqxFAb", json.dumps(cd), None, None, None, "generic"]
        body = json.dumps([entry])
        raw = ")]}'\n" + str(len(body)) + "\n" + body
        opts = _parse_batch_response(raw)
        assert len(opts) >= 1
