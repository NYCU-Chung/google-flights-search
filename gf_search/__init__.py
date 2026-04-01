"""
gf_search — lightweight Google Flights SSR client.

Dependencies: primp, selectolax, rjsonc.
Optional (for Stage 5 Playwright fallback): playwright

Quick start:
    from gf_search import search

    results = search("TPE", "NRT", "2026-08-08")
    for r in results:
        print(r["airlines"], r["price"], r["stops"], "stops")

Multi-city:
    from gf_search import search_multi_city

    results = search_multi_city([
        {"from": "TPE", "to": "NRT", "date": "2026-05-01"},
        {"from": "NRT", "to": "LHR", "date": "2026-05-03"},
        {"from": "LHR", "to": "TPE", "date": "2026-05-10"},
    ])

One-time Google session setup (for regional routes, run once):
    import gf_search
    gf_search.setup()
    # Opens a browser window — sign into Google, then press Enter.
    # After this, all searches return full results automatically.
"""

from .search import search
from .multi_city import search_multi_city
from .builder import build_tfs, build_tfs_multi_city, CITY_ENTITIES
from .setup import setup

__all__ = [
    "search", "search_multi_city",
    "build_tfs", "build_tfs_multi_city", "CITY_ENTITIES",
    "setup",
]
