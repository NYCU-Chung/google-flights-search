"""
Integration tests — hit real Google Flights endpoints.

These tests make actual network requests and may take 10–60 s each.
Run with:   pytest tests/test_integration.py -v -s

Regional airport tests (Stage 5 Playwright) require:
  pip install "google-flights-search[playwright]"
  playwright install chromium
  gf-search-setup   (one-time Google sign-in)
"""

import pytest
import gf_search


# ── schema validator ──────────────────────────────────────────────────────────

def _assert_valid_result(r: dict):
    """Assert a single search result matches the documented schema."""
    assert isinstance(r, dict), "Result must be a dict"
    assert isinstance(r.get("airlines"), list) and r["airlines"], "airlines must be non-empty list"
    assert all(isinstance(a, str) and len(a) == 2 for a in r["airlines"]), \
        f"All airline codes must be 2-char strings: {r['airlines']}"
    assert isinstance(r.get("price"), str), "price must be str"
    if r["price"]:
        parts = r["price"].split()
        assert len(parts) == 2 and parts[1].isdigit(), f"price format invalid: {r['price']}"
    assert isinstance(r.get("stops"), int) and r["stops"] >= 0, "stops must be non-negative int"
    assert isinstance(r.get("segments"), list) and r["segments"], "segments must be non-empty list"
    assert r.get("source") == "gf_search", f"source must be 'gf_search', got: {r.get('source')}"
    assert len(r["segments"]) == r["stops"] + 1, \
        f"Expected {r['stops']+1} segments for {r['stops']} stops, got {len(r['segments'])}"
    for seg in r["segments"]:
        for key in ("from", "to", "flight_no", "departure", "arrival", "duration_min", "plane"):
            assert key in seg, f"Segment missing key: {key}"
        assert isinstance(seg["from"], str) and len(seg["from"]) == 3
        assert isinstance(seg["to"], str)   and len(seg["to"])   == 3
        assert isinstance(seg["duration_min"], (int, float))


# ── popular route (SSR stages 1-3) ───────────────────────────────────────────

@pytest.mark.integration
def test_tpe_nrt_returns_results():
    """TPE→NRT is a high-traffic route; SSR stages should always find results."""
    results = gf_search.search("TPE", "NRT", "2026-08-08")
    assert len(results) > 0, "Expected at least one flight TPE→NRT"
    for r in results:
        _assert_valid_result(r)


@pytest.mark.integration
def test_tpe_nrt_all_sources_gf_search():
    results = gf_search.search("TPE", "NRT", "2026-08-08")
    assert all(r["source"] == "gf_search" for r in results)


@pytest.mark.integration
def test_tpe_nrt_prices_positive():
    results = gf_search.search("TPE", "NRT", "2026-08-08")
    prices = [int(r["price"].split()[1]) for r in results if r["price"]]
    assert all(p > 0 for p in prices), "All prices must be positive"


@pytest.mark.integration
def test_tpe_nrt_sorted_by_price():
    results = gf_search.search("TPE", "NRT", "2026-08-08")
    prices = [int(r["price"].split()[1]) for r in results if r["price"]]
    assert prices == sorted(prices), "Results should be sorted cheapest first"


@pytest.mark.integration
def test_max_results_respected():
    results = gf_search.search("TPE", "NRT", "2026-08-08", max_results=2)
    assert len(results) <= 2


@pytest.mark.integration
def test_business_class():
    results = gf_search.search("TPE", "NRT", "2026-08-08", travel_class="business")
    assert isinstance(results, list)
    # Business class might return fewer results; just check schema if any
    for r in results:
        _assert_valid_result(r)


@pytest.mark.integration
def test_round_trip():
    results = gf_search.search("TPE", "NRT", "2026-08-08", return_date="2026-08-15")
    # Round-trip queries may return [] for one-way only routes; just check schema
    for r in results:
        _assert_valid_result(r)


# ── regional airport (Stage 5 Playwright) ────────────────────────────────────

@pytest.mark.integration
@pytest.mark.slow
def test_rmq_kmj_returns_results():
    """
    RMQ→KMJ is a small-airport route; requires Stage 5 (Playwright + Google session).
    Requires: playwright install chromium && gf-search-setup
    """
    results = gf_search.search("RMQ", "KMJ", "2026-08-09")
    assert len(results) > 0, (
        "Expected at least one flight RMQ→KMJ. "
        "If Stage 5 is unavailable, run: playwright install chromium && gf-search-setup"
    )
    for r in results:
        _assert_valid_result(r)


@pytest.mark.integration
@pytest.mark.slow
def test_rmq_kmj_contains_jx316():
    """JX316 is the direct Starlux flight RMQ→KMJ. Requires Google session."""
    results = gf_search.search("RMQ", "KMJ", "2026-08-09")
    flight_nos = [
        seg["flight_no"]
        for r in results
        for seg in r["segments"]
    ]
    assert "JX316" in flight_nos, (
        f"JX316 not found in {flight_nos}. "
        "Ensure gf-search-setup has been run with a valid Google account."
    )


@pytest.mark.integration
@pytest.mark.slow
def test_rmq_kmj_jx316_is_direct():
    results = gf_search.search("RMQ", "KMJ", "2026-08-09")
    jx316 = next(
        (r for r in results if any(s["flight_no"] == "JX316" for s in r["segments"])),
        None,
    )
    assert jx316 is not None, "JX316 not found"
    assert jx316["stops"] == 0, f"JX316 should be direct, got stops={jx316['stops']}"
    assert "JX" in jx316["airlines"]
