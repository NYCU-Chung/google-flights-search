"""
gf-search — examples / basic_search.py

Run this file directly to see gf-search in action:

    python examples/basic_search.py

Requirements:
    pip install gf-search
    # or, from the repo root:
    pip install -e .
"""

import json
from gf_search import search, build_tfs, CITY_ENTITIES


# ---------------------------------------------------------------------------
# Helper: pretty-print a list of flight results
# ---------------------------------------------------------------------------
def print_results(results: list[dict], label: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")
    if not results:
        print("  (no results — try again, Google SSR may need a retry)")
        return
    for i, r in enumerate(results, 1):
        airlines = ", ".join(r["airlines"]) if r["airlines"] else "Unknown"
        price    = r["price"] or "price N/A"
        stops    = f"{r['stops']} stop(s)"
        segs     = r["segments"]
        dep      = segs[0]["departure"] if segs else "?"
        arr      = segs[-1]["arrival"]  if segs else "?"
        plane    = segs[0].get("plane", "") if segs else ""
        print(f"  [{i}] {airlines}")
        print(f"       {dep}  →  {arr}  |  {stops}  |  {price}")
        if plane:
            print(f"       Aircraft: {plane}")
        if r["stops"] > 0:
            for s in segs:
                print(f"         Segment: {s['from']} → {s['to']}  ({s['duration_min']} min)")
        print()


# ---------------------------------------------------------------------------
# Example 1 — Basic one-way search (major airport pair)
# ---------------------------------------------------------------------------
print("\n[Example 1] Basic one-way search: TPE → NRT on 2026-08-08")
results = search("TPE", "NRT", "2026-08-08")
print_results(results, "TPE → NRT  (economy, one-way)")


# ---------------------------------------------------------------------------
# Example 2 — Small airport search  ← the key differentiator vs fast-flights
#
# fast-flights:  data[3] = null   (returns [])
# gf-search:     data[3] = list   (returns Starlux JX direct + others)
# ---------------------------------------------------------------------------
print("\n[Example 2] Small airport search: RMQ → KMJ on 2026-08-08")
print("  (fast-flights returns nothing here; gf-search uses the correct protobuf)")
results = search("RMQ", "KMJ", "2026-08-08")
print_results(results, "RMQ (Taichung) → KMJ (Kumamoto)  (economy, one-way)")


# ---------------------------------------------------------------------------
# Example 3 — Business class round-trip search
# ---------------------------------------------------------------------------
print("\n[Example 3] Business class round-trip: TPE → LHR")
results = search(
    "TPE",
    "LHR",
    departure_date="2026-09-01",
    return_date="2026-09-15",
    travel_class="business",
    adults=2,
    max_results=3,
)
print_results(results, "TPE → LHR  (business, round-trip, 2 adults)")


# ---------------------------------------------------------------------------
# Example 4 — Build a raw tfs URL (for inspection or custom HTTP requests)
# ---------------------------------------------------------------------------
print("\n[Example 4] Build raw tfs URL parameter")
tfs = build_tfs(
    origin="RMQ",
    destination="KMJ",
    departure_date="2026-08-08",
    return_date="2026-08-15",
    seat=1,     # 1=economy 2=premium-economy 3=business 4=first
    adults=1,
)
url = f"https://www.google.com/travel/flights/search?tfs={tfs}&tfu=EgIIACIA&hl=zh-TW"
print(f"  tfs  = {tfs[:60]}...")
print(f"  URL  = {url[:90]}...")
print("  Paste the full URL into Chrome to verify results match.")


# ---------------------------------------------------------------------------
# Example 5 — Extend CITY_ENTITIES with a new airport
#
# To find an entity ID:
#   1. Open Google Flights in Chrome and search for your airport.
#   2. Open DevTools → Network → filter "flights/search".
#   3. Copy the `tfs` query parameter and decode it (base64 → protobuf).
#   4. The entity ID is the string in Airport.field_2.
# ---------------------------------------------------------------------------
print("\n[Example 5] Extend CITY_ENTITIES with Okinawa Naha (OKA)")
print(f"  Before: CITY_ENTITIES = {json.dumps(CITY_ENTITIES, ensure_ascii=False)}")

CITY_ENTITIES["OKA"] = "/m/0h7r_"   # Naha city entity ID

print(f"  After:  CITY_ENTITIES = {json.dumps(CITY_ENTITIES, ensure_ascii=False)}")
print("  Now search('OKA', ...) will use the city entity instead of the IATA code.")

# Verify the new entry is picked up by build_tfs
tfs_oka = build_tfs("OKA", "TPE", "2026-08-08")
print(f"  tfs for OKA→TPE = {tfs_oka[:60]}...")
