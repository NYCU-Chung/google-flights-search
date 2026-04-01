"""
Unit tests for gf_search.builder — protobuf tfs encoder.

All tests are offline (no network); they verify that the encoded bytes
contain the required fields at the binary level.
"""

import base64
import pytest
from gf_search.builder import build_tfs, build_tfs_selected, build_tfs_multi_city, build_tfs_multi_city_partial, CITY_ENTITIES


# ── helpers ──────────────────────────────────────────────────────────────────

def decode_tfs(tfs: str) -> bytes:
    """Decode a tfs string back to raw protobuf bytes."""
    padding = "=" * (-len(tfs) % 4)
    return base64.urlsafe_b64decode(tfs + padding)


def parse_varints(data: bytes) -> list[tuple[int, int, bytes]]:
    """
    Shallow-parse protobuf fields.
    Returns list of (field_no, wire_type, raw_value_bytes).
    wire_type 0 = varint, 2 = length-delimited.
    """
    results = []
    i = 0
    while i < len(data):
        tag_val = 0
        shift = 0
        while True:
            b = data[i]; i += 1
            tag_val |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                break
        fn = tag_val >> 3
        wt = tag_val & 7
        if wt == 0:
            val = 0; shift = 0
            while True:
                b = data[i]; i += 1
                val |= (b & 0x7F) << shift
                shift += 7
                if not (b & 0x80):
                    break
            results.append((fn, 0, val.to_bytes((val.bit_length() + 7) // 8 or 1, "little")))
        elif wt == 2:
            length = 0; shift = 0
            while True:
                b = data[i]; i += 1
                length |= (b & 0x7F) << shift
                shift += 7
                if not (b & 0x80):
                    break
            results.append((fn, 2, data[i:i + length]))
            i += length
        else:
            break  # unsupported wire type; stop
    return results


def find_field(fields, field_no, wire_type=None):
    """Return first matching field value bytes, or None."""
    for fn, wt, val in fields:
        if fn == field_no and (wire_type is None or wt == wire_type):
            return val
    return None


def find_all_fields(fields, field_no, wire_type=None):
    return [val for fn, wt, val in fields if fn == field_no and (wire_type is None or wt == wire_type)]


def varint_value(data: bytes) -> int:
    val = 0
    for i, b in enumerate(data):
        val |= (b & 0x7F) << (7 * i)
    return val


# ── build_tfs ─────────────────────────────────────────────────────────────────

class TestBuildTfs:

    def test_returns_string(self):
        tfs = build_tfs("TPE", "NRT", "2026-08-08")
        assert isinstance(tfs, str)

    def test_url_safe_base64(self):
        tfs = build_tfs("TPE", "NRT", "2026-08-08")
        assert "+" not in tfs
        assert "/" not in tfs
        assert "=" not in tfs  # no padding
        decode_tfs(tfs)  # must not raise

    def test_field1_equals_28(self):
        raw = decode_tfs(build_tfs("TPE", "NRT", "2026-08-08"))
        fields = parse_varints(raw)
        f1 = find_field(fields, 1, wire_type=0)
        assert f1 is not None
        assert varint_value(f1) == 28

    def test_field2_equals_2(self):
        raw = decode_tfs(build_tfs("TPE", "NRT", "2026-08-08"))
        fields = parse_varints(raw)
        f2 = find_field(fields, 2, wire_type=0)
        assert f2 is not None
        assert varint_value(f2) == 2

    def test_field16_all_results_flag(self):
        """field 16 must contain the 11-byte INT64_MAX sub-message."""
        _EXPECTED = b'\x08' + b'\xff' * 9 + b'\x01'
        raw = decode_tfs(build_tfs("RMQ", "KMJ", "2026-08-09"))
        fields = parse_varints(raw)
        f16 = find_field(fields, 16, wire_type=2)
        assert f16 == _EXPECTED, f"field 16 mismatch: {f16!r}"

    def test_one_way_trip_type(self):
        """field 19 = 2 for one-way."""
        raw = decode_tfs(build_tfs("TPE", "NRT", "2026-08-08", return_date=None))
        fields = parse_varints(raw)
        f19 = find_field(fields, 19, wire_type=0)
        assert f19 is not None
        assert varint_value(f19) == 2

    def test_round_trip_type(self):
        """field 19 = 1 for round-trip."""
        raw = decode_tfs(build_tfs("TPE", "NRT", "2026-08-08", return_date="2026-08-15"))
        fields = parse_varints(raw)
        f19 = find_field(fields, 19, wire_type=0)
        assert f19 is not None
        assert varint_value(f19) == 1

    def test_round_trip_has_two_flight_data_blocks(self):
        """field 3 (FlightData) appears twice in a round-trip."""
        raw = decode_tfs(build_tfs("TPE", "NRT", "2026-08-08", return_date="2026-08-15"))
        fields = parse_varints(raw)
        f3_all = find_all_fields(fields, 3, wire_type=2)
        assert len(f3_all) == 2

    def test_one_way_has_one_flight_data_block(self):
        raw = decode_tfs(build_tfs("TPE", "NRT", "2026-08-08"))
        fields = parse_varints(raw)
        f3_all = find_all_fields(fields, 3, wire_type=2)
        assert len(f3_all) == 1

    @pytest.mark.parametrize("seat_name,seat_no", [
        ("economy", 1),
        ("premium-economy", 2),
        ("business", 3),
        ("first", 4),
    ])
    def test_seat_class(self, seat_name, seat_no):
        from gf_search.fetcher import _SEAT_MAP
        seat = _SEAT_MAP.get(seat_name, 1)
        raw = decode_tfs(build_tfs("TPE", "NRT", "2026-08-08", seat=seat))
        fields = parse_varints(raw)
        f9 = find_field(fields, 9, wire_type=0)
        assert f9 is not None
        assert varint_value(f9) == seat_no

    def test_adults_count(self):
        """Each adult is a separate field 8 = 1."""
        for n_adults in (1, 2, 3):
            raw = decode_tfs(build_tfs("TPE", "NRT", "2026-08-08", adults=n_adults))
            fields = parse_varints(raw)
            f8_all = find_all_fields(fields, 8, wire_type=0)
            assert len(f8_all) == n_adults, f"Expected {n_adults} field-8 entries, got {len(f8_all)}"

    def test_origin_encoded_in_flight_data(self):
        raw = decode_tfs(build_tfs("RMQ", "KMJ", "2026-08-09"))
        assert b"RMQ" in raw

    def test_destination_encoded_in_flight_data(self):
        raw = decode_tfs(build_tfs("RMQ", "KMJ", "2026-08-09"))
        assert b"KMJ" in raw

    def test_date_encoded_in_flight_data(self):
        raw = decode_tfs(build_tfs("TPE", "NRT", "2026-08-08"))
        assert b"2026-08-08" in raw

    def test_different_routes_give_different_tfs(self):
        a = build_tfs("TPE", "NRT", "2026-08-08")
        b = build_tfs("RMQ", "KMJ", "2026-08-09")
        assert a != b


# ── build_tfs_selected ────────────────────────────────────────────────────────

class TestBuildTfsSelected:

    def test_returns_string(self):
        tfs = build_tfs_selected("KMJ", "RMQ", "2026-08-02", "2026-08-09", "JX317")
        assert isinstance(tfs, str)

    def test_round_trip_type(self):
        raw = decode_tfs(
            build_tfs_selected("KMJ", "RMQ", "2026-08-02", "2026-08-09", "JX317")
        )
        fields = parse_varints(raw)
        f19 = find_field(fields, 19, wire_type=0)
        assert f19 is not None
        assert varint_value(f19) == 1  # round-trip

    def test_carrier_encoded(self):
        raw = decode_tfs(
            build_tfs_selected("KMJ", "RMQ", "2026-08-02", "2026-08-09", "JX317")
        )
        assert b"JX" in raw

    def test_flight_number_encoded(self):
        raw = decode_tfs(
            build_tfs_selected("KMJ", "RMQ", "2026-08-02", "2026-08-09", "JX317")
        )
        assert b"317" in raw

    def test_has_two_flight_data_blocks(self):
        raw = decode_tfs(
            build_tfs_selected("KMJ", "RMQ", "2026-08-02", "2026-08-09", "JX317")
        )
        fields = parse_varints(raw)
        f3_all = find_all_fields(fields, 3, wire_type=2)
        assert len(f3_all) == 2


# ── build_tfs_multi_city ──────────────────────────────────────────────────────

class TestBuildTfsMultiCity:

    def test_returns_string(self):
        segs = [
            {"from": "TPE", "to": "NRT", "date": "2026-05-01"},
            {"from": "NRT", "to": "LHR", "date": "2026-05-03"},
        ]
        assert isinstance(build_tfs_multi_city(segs), str)

    def test_multi_city_trip_type(self):
        segs = [
            {"from": "TPE", "to": "NRT", "date": "2026-05-01"},
            {"from": "NRT", "to": "LHR", "date": "2026-05-03"},
        ]
        raw = decode_tfs(build_tfs_multi_city(segs))
        fields = parse_varints(raw)
        f19 = find_field(fields, 19, wire_type=0)
        assert varint_value(f19) == 3

    def test_segment_count(self):
        """Each segment produces one field 3."""
        segs = [
            {"from": "TPE", "to": "NRT", "date": "2026-05-01"},
            {"from": "NRT", "to": "LHR", "date": "2026-05-03"},
            {"from": "LHR", "to": "TPE", "date": "2026-05-10"},
        ]
        raw = decode_tfs(build_tfs_multi_city(segs))
        fields = parse_varints(raw)
        f3_all = find_all_fields(fields, 3, wire_type=2)
        assert len(f3_all) == 3

    def test_has_field16(self):
        """Multi-city must include field 16 (all-results flag), same as browser-observed tfs."""
        segs = [
            {"from": "TPE", "to": "NRT", "date": "2026-05-01"},
            {"from": "NRT", "to": "LHR", "date": "2026-05-03"},
        ]
        raw = decode_tfs(build_tfs_multi_city(segs))
        fields = parse_varints(raw)
        f16 = find_field(fields, 16)
        assert f16 is not None


# ── build_tfs_multi_city_partial ──────────────────────────────────────────────

class TestBuildTfsMultiCityPartial:

    def test_returns_string(self):
        segs = [
            {"from": "NRT", "to": "TPE", "date": "2026-08-08"},
            {"from": "TPE", "to": "HKG", "date": "2026-08-12"},
        ]
        assert isinstance(build_tfs_multi_city_partial(segs, {}), str)

    def test_no_selection_matches_plain_multi_city(self):
        """With empty selections, partial == plain multi-city."""
        segs = [
            {"from": "NRT", "to": "TPE", "date": "2026-08-08"},
            {"from": "TPE", "to": "HKG", "date": "2026-08-12"},
        ]
        assert build_tfs_multi_city_partial(segs, {}) == build_tfs_multi_city(segs)

    def test_selected_leg_has_field4(self):
        """A selected leg's field3 must contain field4 (selection sub-message)."""
        segs = [
            {"from": "NRT", "to": "TPE", "date": "2026-08-08"},
            {"from": "TPE", "to": "HKG", "date": "2026-08-12"},
        ]
        raw = decode_tfs(build_tfs_multi_city_partial(segs, {0: "CI107"}))
        fields = parse_varints(raw)
        f3_list = find_all_fields(fields, 3, wire_type=2)
        assert len(f3_list) == 2
        # Leg 0 field3 must contain field4 (carrier selection)
        leg0_fields = parse_varints(f3_list[0])
        f4 = find_field(leg0_fields, 4, wire_type=2)
        assert f4 is not None, "leg0 should have field4 (flight selection)"
        # Leg 1 field3 must NOT contain field4 (not yet selected)
        leg1_fields = parse_varints(f3_list[1])
        f4_leg1 = find_field(leg1_fields, 4, wire_type=2)
        assert f4_leg1 is None, "leg1 should NOT have field4"

    def test_carrier_encoded_in_selected_leg(self):
        segs = [
            {"from": "NRT", "to": "TPE", "date": "2026-08-08"},
            {"from": "TPE", "to": "HKG", "date": "2026-08-12"},
        ]
        raw = decode_tfs(build_tfs_multi_city_partial(segs, {0: "CI107"}))
        assert b"CI" in raw
        assert b"107" in raw

    def test_multi_city_trip_type_preserved(self):
        segs = [
            {"from": "NRT", "to": "TPE", "date": "2026-08-08"},
            {"from": "TPE", "to": "HKG", "date": "2026-08-12"},
        ]
        raw = decode_tfs(build_tfs_multi_city_partial(segs, {0: "CI107"}))
        fields = parse_varints(raw)
        f19 = find_field(fields, 19, wire_type=0)
        assert varint_value(f19) == 3   # MULTI_CITY

    def test_partial_differs_from_plain(self):
        segs = [
            {"from": "NRT", "to": "TPE", "date": "2026-08-08"},
            {"from": "TPE", "to": "HKG", "date": "2026-08-12"},
        ]
        plain = build_tfs_multi_city(segs)
        partial = build_tfs_multi_city_partial(segs, {0: "CI107"})
        assert plain != partial
