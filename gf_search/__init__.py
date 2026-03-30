"""
gf_search — lightweight Google Flights SSR client.

Zero Google session, zero Playwright.
Dependencies: primp, selectolax, rjsonc.

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
"""

from .search import search
from .multi_city import search_multi_city
from .builder import build_tfs, build_tfs_multi_city, CITY_ENTITIES

__all__ = ["search", "search_multi_city", "build_tfs", "build_tfs_multi_city", "CITY_ENTITIES"]
