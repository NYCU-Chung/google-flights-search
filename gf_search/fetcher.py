"""
Google Flights SSR fetcher using primp (no Playwright, no Google session needed).

Reads both data[2] (Best Flights) and data[3] (Other Flights) from the SSR
response to ensure full airline coverage, including Cathay Pacific (CX) which
appears in data[2] but not data[3].

Optional: if Chrome is installed on Windows with a Google account, session
cookies are injected automatically for additional session-aware content.
"""

from __future__ import annotations

import ast


def _price_key(f: dict) -> int:
    try:
        return int(f["price"].split()[-1]) if f.get("price") else 10**9
    except (ValueError, IndexError):
        return 10**9


_SEAT_MAP: dict[str, int] = {
    "economy": 1,
    "premium-economy": 2,
    "premium economy": 2,
    "business": 3,
    "first": 4,
}

_GF_SEARCH_URL = "https://www.google.com/travel/flights/search"


def fetch(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None = None,
    seat: str = "economy",
    adults: int = 1,
    max_results: int = 5,
    currency: str = "TWD",
    max_stops: int | None = None,
) -> list[dict]:
    """
    Fetch Google Flights results via primp SSR (impersonates Chrome, no browser needed).

    Returns a list of flight dicts as defined in parser.parse_js().
    Returns [] if primp/selectolax is unavailable or if the response contains no data.
    """
    # ── Stage 0: Chrome-authenticated cache ──────────────────────────────────
    # If ~/.gf_search/chrome_cache.json exists and is < 24 h old, use it.
    # The cache is populated by running JS fetches inside Chrome with the user's
    # Google session, which returns warm SSR for small/regional airports.
    import json as _json
    import os as _os
    from datetime import datetime as _dt, timezone as _tz

    _cache_path = _os.path.expanduser('~/.gf_search/chrome_cache.json')
    try:
        with open(_cache_path, encoding='utf-8') as _f:
            _cache = _json.load(_f)
        _fetched_at = _dt.fromisoformat(_cache['_fetched_at'])
        if _fetched_at.tzinfo is None:
            _fetched_at = _fetched_at.replace(tzinfo=_tz.utc)
        _age_hours = (_dt.now(_tz.utc) - _fetched_at).total_seconds() / 3600
        if _age_hours < 24:
            _key = f'{origin.upper()}_{destination.upper()}_{departure_date}'
            if _key in _cache.get('data', {}):
                _results: list[dict] = []
                for _fl in _cache['data'][_key]:
                    _segs: list[dict] = []
                    for _sg in _fl['segs']:
                        try:
                            _dep_dt = _dt.fromisoformat(_sg['d'])
                            _arr_dt = _dt.fromisoformat(_sg['a'])
                            _dur = int((_arr_dt - _dep_dt).total_seconds() / 60)
                        except Exception:
                            _dur = 0
                        _segs.append({
                            'from': _sg['f'],
                            'to': _sg['t'],
                            'flight_no': _sg['fn'],
                            'departure': _sg['d'],
                            'arrival': _sg['a'],
                            'duration_min': _dur,
                            'plane': '',
                        })
                    _airlines = list(dict.fromkeys(s['flight_no'][:2] for s in _segs))
                    _results.append({
                        'airlines': _airlines,
                        'price': f'{currency} {_fl["p"]}',
                        'stops': _fl['s'],
                        'segments': _segs,
                        'source': 'gf_search',
                    })
                if _results:
                    _results.sort(key=_price_key)
                    return _results[:max_results]
    except Exception:
        pass

    try:
        from primp import Client
        from selectolax.lexbor import LexborHTMLParser
    except ImportError:
        return []

    from .builder import build_tfs
    from .parser import parse_js

    seat_no = _SEAT_MAP.get(seat.lower(), 1)

    tfs = build_tfs(
        origin.upper(), destination.upper(),
        departure_date, return_date,
        seat=seat_no, adults=adults,
    )

    import time as _time

    params = {"tfs": tfs, "tfu": "EgIIACIA", "hl": "zh-TW"}
    headers = {"Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"}

    # Optional: inject session cookies for personalised SSR results.
    # Priority: Chrome CDP/SQLite → session_cookies.json (from gf-search-setup)
    try:
        from ._chrome_cookies import get_google_session_cookie
        cookie_str = get_google_session_cookie()
        if not cookie_str:
            try:
                from .setup import load_session_cookies
                sess = load_session_cookies()
                if sess:
                    cookie_str = "; ".join(
                        f"{c['name']}={c['value']}" for c in sess
                        if isinstance(c, dict) and c.get("value")
                    )
            except Exception:
                pass
        if cookie_str:
            headers["Cookie"] = cookie_str
    except Exception:
        pass

    # Google SSR is non-deterministic: each response may return a different
    # subset of flights. Fetch up to 3 times and merge all unique results so
    # low-frequency airlines (e.g. CX, JX) are not silently dropped.
    #
    # Use ONE client across all attempts so that cookies received from Google
    # in the first response are automatically sent in subsequent requests —
    # this progressively warms the session and increases result completeness.
    seen: set[tuple] = set()
    merged: list[dict] = []

    client = _make_client()

    for attempt in range(3):
        if attempt > 0:
            _time.sleep(1.5)
        try:
            res = client.get(_GF_SEARCH_URL, params=params, headers=headers)
        except Exception:
            continue

        try:
            html_parser = LexborHTMLParser(res.text)
            ds1 = html_parser.css_first(r"script.ds\:1")
            if not ds1:
                continue
            txt = ds1.text()
            if "data:" not in txt:
                continue
        except Exception:
            continue

        for f in parse_js(txt, currency=currency):
            f.pop("_token", None)   # internal field; not for callers
            # Deduplicate by (airlines, first departure datetime)
            key = (
                tuple(f["airlines"]),
                f["segments"][0].get("departure", "") if f["segments"] else "",
            )
            if key not in seen:
                seen.add(key)
                merged.append(f)

    # ── Round-trip Stage 5: Playwright headless for round-trip ──────────────
    # For round-trip searches where SSR returns empty (small airports),
    # try Playwright headless FIRST with the original round-trip URL.
    # This preserves round-trip-only results (e.g. JX RMQ-KMJ direct only
    # appears in round-trip, not one-way).
    if not merged and return_date is not None:
        try:
            import asyncio as _asyncio_rt
            import re as _re_rt
            import json as _json_rt
            from pathlib import Path as _Path_rt
            from playwright.async_api import async_playwright as _async_pw_rt
            from .multi_city import _parse_flight_section as _pfs_rt

            _pw_profile_rt = str(_Path_rt.home() / ".flight_agent" / "playwright_profile")
            _Path_rt(_pw_profile_rt).mkdir(parents=True, exist_ok=True)
            _gf_url_rt = f"https://www.google.com/travel/flights/search?tfs={tfs}&tfu=EgIIACIA&hl=zh-TW"

            def _parse_intercepted_rt(raw: str) -> list[dict]:
                if not raw.startswith(")]}'"):
                    return []
                stripped = raw[4:].lstrip()
                m = _re_rt.match(r"[0-9a-fA-F]+\r?\n", stripped)
                if m:
                    stripped = stripped[m.end():]
                try:
                    decoder = _json_rt.JSONDecoder()
                    outer, _ = decoder.raw_decode(stripped)
                except Exception:
                    return []
                entry = next(
                    (e for e in outer if isinstance(e, list) and len(e) >= 3
                     and e[0] == "wrb.fr" and e[2]),
                    None,
                )
                if not entry:
                    return []
                try:
                    cd = _json_rt.loads(entry[2])
                except Exception:
                    return []
                opts: list[dict] = []
                for _idx in [2, 3]:
                    if len(cd) > _idx and cd[_idx] and isinstance(cd[_idx], list):
                        opts.extend(_pfs_rt(cd[_idx][0]))
                return opts

            async def _pw_fetch_rt() -> list[dict]:
                _collected: list[dict] = []
                _launch_kwargs = dict(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                    timeout=60_000,
                )
                async with _async_pw_rt() as _pw:
                    try:
                        _ctx = await _pw.chromium.launch_persistent_context(
                            _pw_profile_rt, channel="chrome", **_launch_kwargs
                        )
                    except Exception:
                        _ctx = await _pw.chromium.launch_persistent_context(
                            _pw_profile_rt, **_launch_kwargs
                        )
                    try:
                        from .setup import load_session_cookies as _lsc_rt
                        _sess = _lsc_rt()
                        if _sess:
                            await _ctx.add_cookies(_sess)
                        else:
                            from ._chrome_cookies import get_chrome_cookies_playwright as _gcc_rt
                            _cks = _gcc_rt()
                            if _cks:
                                await _ctx.add_cookies(_cks)
                    except Exception:
                        pass
                    _page = _ctx.pages[0] if _ctx.pages else await _ctx.new_page()

                    async def _on_resp(_resp):
                        _url = _resp.url
                        if (
                            ("FlightsFrontend" in _url or "batchexecute" in _url)
                            and "browserinfo" not in _url
                            and "manifest" not in _url
                            and "bscframe" not in _url
                        ):
                            try:
                                _body = await _resp.text()
                                _collected.extend(_parse_intercepted_rt(_body))
                            except Exception:
                                pass

                    _page.on("response", _on_resp)
                    await _page.goto(_gf_url_rt, wait_until="networkidle", timeout=45_000)
                    await _page.wait_for_timeout(8_000)
                    if not _collected:
                        await _page.wait_for_timeout(5_000)
                    await _ctx.close()
                return _collected

            try:
                _loop_rt = _asyncio_rt.get_event_loop()
                if _loop_rt.is_running():
                    import concurrent.futures as _cf_rt
                    with _cf_rt.ThreadPoolExecutor(max_workers=1) as _ex:
                        _pw_results = _ex.submit(_asyncio_rt.run, _pw_fetch_rt()).result(timeout=75)
                else:
                    _pw_results = _asyncio_rt.run(_pw_fetch_rt())
            except Exception:
                _pw_results = []

            for _f in _pw_results:
                _f.pop("_token", None)
                _f["source"] = "gf_search"
                _f["price"] = f"{currency} {_f['price']}" if isinstance(_f.get("price"), int) else _f.get("price", "")
                _key = (
                    tuple(_f["airlines"]),
                    _f["segments"][0].get("departure", "") if _f["segments"] else "",
                )
                if _key not in seen:
                    seen.add(_key)
                    merged.append(_f)
        except ImportError:
            pass
        except Exception:
            pass

    # ── Round-trip decomposition fallback ────────────────────────────────────
    # If Stage 5 didn't produce results (Playwright not installed, or failed),
    # decompose into two one-way searches as last resort.
    # WARNING: one-way prices are typically higher than half of a round-trip fare.
    if not merged and return_date is not None:
        outbound = fetch(
            origin, destination, departure_date, None,
            seat, adults, max_results, currency, max_stops,
        )
        inbound = fetch(
            destination, origin, return_date, None,
            seat, adults, max_results, currency, max_stops,
        )
        for f in outbound:
            f["direction"] = "outbound"
            f["_price_note"] = "one-way price (round-trip may be cheaper)"
        for f in inbound:
            f["direction"] = "inbound"
            f["_price_note"] = "one-way price (round-trip may be cheaper)"
        return outbound + inbound

    # Small-airport-as-origin fallback.
    #
    # Google only populates SSR cache for the DESTINATION airport, so one-way
    # queries where the origin is a small/regional airport return empty.
    # Two-stage recovery:
    #
    # Stage 3 – tfu-based return-leg fetch (primary fallback):
    #   Google Flights shows return options only after a user selects an outbound
    #   flight.  The selection is encoded as:
    #     • tfs: outbound FlightData gains a "selected flight" sub-message (field 4)
    #            containing carrier IATA + flight number
    #     • tfu: wraps the outbound flight's price_info[1] booking token
    #   We replicate this by:
    #     1. Fetching SSR for dest→origin (1-way) to obtain booking tokens.
    #     2. Building a modified round-trip tfs (with field 4) + tfu.
    #     3. Fetching that URL → SSR now contains origin→dest return options.
    #   Only direct (non-connecting) outbound flights are used; pipe-joined
    #   multi-segment flight_ids require a different field-4 layout.
    #
    # Stage 2 – batchexecute chain (secondary fallback):
    #   Use the same reversed round-trip tfs to extract orig_inner from the page.
    #   orig_inner is always embedded even when SSR flight data is null.
    #   Then chain two batchexecute calls:
    #     leg 0 = dest→origin (outbound) → get carrier tokens
    #     leg 1 = origin→dest (return)   → get actual options for the requested route
    #   Filter leg-1 results to first segment from == origin.
    if not merged and return_date is None:
        import re as _re
        import base64 as _b64
        from datetime import date as _date, timedelta as _timedelta

        try:
            dep = _date.fromisoformat(departure_date)
            dummy_outbound = (dep - _timedelta(days=7)).isoformat()
        except Exception:
            dummy_outbound = None

        # ── Helpers ───────────────────────────────────────────────────────────
        from ._utils import _varint

        def _decode_flight_id(token_b64: str) -> str:
            """Return the flight-id string (field 2) from a booking token."""
            try:
                inner = _b64.urlsafe_b64decode(token_b64 + "=" * (-len(token_b64) % 4))
                pos = 0
                while pos < len(inner):
                    tag_val = 0; shift = 0
                    while True:
                        b = inner[pos]; pos += 1
                        tag_val |= (b & 0x7F) << shift; shift += 7
                        if not (b & 0x80): break
                    fn, wt = tag_val >> 3, tag_val & 7
                    if wt == 2:
                        l = 0; shift = 0
                        while True:
                            b = inner[pos]; pos += 1
                            l |= (b & 0x7F) << shift; shift += 7
                            if not (b & 0x80): break
                        raw = inner[pos:pos + l]; pos += l
                        if fn == 2:
                            return raw.decode("utf-8", errors="replace")
                    elif wt == 0:
                        while True:
                            b = inner[pos]; pos += 1
                            if not (b & 0x80): break
                    else:
                        break
            except Exception:
                pass
            return ""

        def _make_tfu(token_b64: str) -> str:
            """Wrap a price_info[1] booking token into a tfu protobuf."""
            tok = token_b64.encode("utf-8")
            raw = (
                b"\x0a" + _varint(len(tok)) + tok
                + b"\x12\x02\x08\x00"   # field 2: {field1: 0}
                + b"\x22\x00"           # field 4: ""
            )
            return _b64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

        # ── Stage 3: tfu-based return-leg fetch ───────────────────────────────
        if dummy_outbound:
            from .builder import build_tfs_selected

            fwd_params = {**params, "tfu": "EgIIACIA"}
            rev_html: str = ""

            for attempt in range(3):
                if attempt > 0:
                    _time.sleep(1.5)
                # Fetch dest→origin one-way on dummy_outbound to get booking tokens.
                tfs_fwd = build_tfs(
                    destination.upper(), origin.upper(),
                    dummy_outbound, None,
                    seat=seat_no, adults=adults,
                )
                try:
                    res = client.get(
                        _GF_SEARCH_URL,
                        params={**fwd_params, "tfs": tfs_fwd},
                        headers=headers,
                    )
                    rev_html = res.text
                except Exception:
                    continue
                try:
                    html_parser = LexborHTMLParser(res.text)
                    ds1 = html_parser.css_first(r"script.ds\:1")
                    if not ds1:
                        continue
                    txt = ds1.text()
                    if "data:" not in txt:
                        continue
                except Exception:
                    continue

                # Collect (token, flight_id) pairs; prefer direct flights.
                fwd_opts: list[tuple[str, str]] = []
                for f in parse_js(txt, currency=currency):
                    token = f.get("_token", "")
                    if not token:
                        continue
                    fid = _decode_flight_id(token)
                    if not fid or "|" in fid:   # skip connecting flights
                        continue
                    fwd_opts.append((token, fid))

                if not fwd_opts:
                    continue

                # Try each outbound option until we get return-leg results.
                for token, fid in fwd_opts[:5]:
                    try:
                        tfu       = _make_tfu(token)
                        tfs_mod   = build_tfs_selected(
                            destination.upper(), origin.upper(),
                            dummy_outbound, departure_date,
                            fid,
                            seat=seat_no, adults=adults,
                        )
                        _time.sleep(0.5)
                        res2 = client.get(
                            _GF_SEARCH_URL,
                            params={**params, "tfs": tfs_mod, "tfu": tfu},
                            headers=headers,
                        )
                        html_parser2 = LexborHTMLParser(res2.text)
                        ds1_2 = html_parser2.css_first(r"script.ds\:1")
                        if not ds1_2:
                            continue
                        txt2 = ds1_2.text()
                        if "data:" not in txt2:
                            continue
                        for f in parse_js(txt2, currency=currency):
                            f.pop("_token", None)
                            if not f["segments"]:
                                continue
                            if f["segments"][0].get("from", "").upper() != origin.upper():
                                continue
                            key = (
                                tuple(f["airlines"]),
                                f["segments"][0].get("departure", ""),
                            )
                            if key not in seen:
                                seen.add(key)
                                merged.append(f)
                    except Exception:
                        continue
                    if merged:
                        break
                if merged:
                    break

        # Strip internal _token field from any results already in merged
        for f in merged:
            f.pop("_token", None)

        # ── Stage 2: batchexecute chain ────────────────────────────────────
        if not merged and rev_html:
            try:
                import copy as _copy
                from .multi_city import _do_batch

                af_m = _re.search(
                    r"'ds:1'\s*:\s*\{id:'LqxFAb',request:(\[.+?\])\}",
                    rev_html, _re.DOTALL,
                )
                if af_m:
                    inner_raw = (
                        af_m.group(1)
                        .replace("null", "None")
                        .replace("true", "True")
                        .replace("false", "False")
                    )
                    try:
                        orig_inner = ast.literal_eval(inner_raw)[1]
                    except (ValueError, SyntaxError):
                        orig_inner = None

                    if orig_inner is not None:
                        legs_in_req = orig_inner[13]
                        at_m = _re.search(r'"SNlM0e":"([^"]+)"', rev_html)
                        at_token = at_m.group(1) if at_m else ""

                        # leg 0: dest→origin (outbound of the reversed round-trip)
                        leg0_opts = _do_batch(
                            client, orig_inner, legs_in_req, at_token, None, 0
                        )

                        # leg 1: origin→dest (return = the route we actually want)
                        for opt in sorted(leg0_opts, key=lambda x: x.get("price") or 0)[:5]:
                            leg1_opts = _do_batch(
                                client, orig_inner, legs_in_req, at_token,
                                opt["token"], 1,
                            )
                            for f in leg1_opts:
                                if not f["segments"]:
                                    continue
                                if f["segments"][0].get("from", "").upper() != origin.upper():
                                    continue
                                key = (
                                    tuple(f["airlines"]),
                                    f["segments"][0].get("departure", ""),
                                )
                                if key not in seen:
                                    seen.add(key)
                                    price_val = f.get("price")
                                    merged.append({
                                        "airlines": f["airlines"],
                                        "price":    f"{currency} {price_val}" if price_val else "",
                                        "stops":    f["stops"],
                                        "segments": f["segments"],
                                        "source":   "gf_search",
                                    })
            except Exception:
                pass

    # ── Stage 5: Playwright + auto-bootstrapped Chrome session ──────────────
    # Renders Google Flights in a real browser (JavaScript executes, so
    # batchexecute / GetShoppingResults calls fire with proper at_token).
    # Network responses are intercepted and parsed directly — no airline-specific
    # code, works for any route Google knows about.
    #
    # Auto-bootstrap: creates ~/.flight_agent/playwright_profile on first run.
    # Tries system Chrome first (channel="chrome"); falls back to the Playwright-
    # bundled Chromium binary so it works even if Chrome is not installed.
    # Requires: playwright  (pip install playwright && playwright install chromium)
    # Also try Stage 5 when results exist but ALL prices are empty (e.g. JX RMQ-KMJ).
    _all_priceless = merged and all(not f.get("price") for f in merged)
    if not merged or _all_priceless:
        try:
            import asyncio as _asyncio
            import re as _re5
            import json as _json5
            from pathlib import Path as _Path
            from playwright.async_api import async_playwright as _async_playwright
            from .multi_city import _parse_flight_section as _pfs

            _pw_profile = str(_Path.home() / ".flight_agent" / "playwright_profile")
            # Auto-create the profile directory; Playwright will initialise it on
            # first run (no manual bootstrap step required).
            _Path(_pw_profile).mkdir(parents=True, exist_ok=True)

            _gf_url = (
                f"https://www.google.com/travel/flights/search"
                f"?tfs={tfs}&tfu=EgIIACIA&hl=zh-TW"
            )

            def _parse_intercepted(raw: str) -> list[dict]:
                """Parse a batchexecute/GetShoppingResults response body."""
                if not raw.startswith(")]}'"):
                    return []
                stripped = raw[4:].lstrip()
                # Strip HTTP chunk-size line if present (hex digits + newline)
                m = _re5.match(r"[0-9a-fA-F]+\r?\n", stripped)
                if m:
                    stripped = stripped[m.end():]
                try:
                    decoder = _json5.JSONDecoder()
                    outer, _ = decoder.raw_decode(stripped)
                except Exception:
                    return []
                entry = next(
                    (e for e in outer
                     if isinstance(e, list) and len(e) >= 3
                     and e[0] == "wrb.fr" and e[2]),
                    None,
                )
                if not entry:
                    return []
                try:
                    cd = _json5.loads(entry[2])
                except Exception:
                    return []
                opts: list[dict] = []
                for _idx in [2, 3]:
                    if len(cd) > _idx and cd[_idx] and isinstance(cd[_idx], list):
                        opts.extend(_pfs(cd[_idx][0]))
                return opts

            async def _pw_fetch() -> list[dict]:
                _collected: list[dict] = []
                _launch_kwargs: dict = dict(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                    timeout=60_000,
                )
                async with _async_playwright() as _pw:
                    # Try system Chrome first; fall back to Playwright-bundled
                    # Chromium so this works on machines without Chrome installed.
                    try:
                        _ctx = await _pw.chromium.launch_persistent_context(
                            _pw_profile, channel="chrome", **_launch_kwargs
                        )
                    except Exception:
                        _ctx = await _pw.chromium.launch_persistent_context(
                            _pw_profile, **_launch_kwargs
                        )

                    # Inject Google session cookies so GetShoppingResults returns
                    # full flight data.  Priority order:
                    #   1. ~/.flight_agent/session_cookies.json  (gf_search.setup())
                    #   2. Chrome's cookie DB (when Chrome is NOT running)
                    try:
                        from .setup import load_session_cookies as _lsc
                        _sess_cks = _lsc()
                        if _sess_cks:
                            await _ctx.add_cookies(_sess_cks)
                        else:
                            from ._chrome_cookies import get_chrome_cookies_playwright as _gcc_pw
                            _chrome_cks = _gcc_pw()
                            if _chrome_cks:
                                await _ctx.add_cookies(_chrome_cks)
                    except Exception:
                        pass

                    _page = _ctx.pages[0] if _ctx.pages else await _ctx.new_page()

                    async def _on_response(_resp):
                        _url = _resp.url
                        if (
                            ("FlightsFrontend" in _url or "batchexecute" in _url)
                            and "browserinfo" not in _url
                            and "manifest" not in _url
                            and "bscframe" not in _url
                        ):
                            try:
                                _body = await _resp.text()
                                _collected.extend(_parse_intercepted(_body))
                            except Exception:
                                pass

                    _page.on("response", _on_response)
                    await _page.goto(_gf_url, wait_until="networkidle", timeout=45_000)
                    # Wait longer for Google's on-demand computation to complete.
                    # Niche routes (e.g. RMQ-KMJ) take extra time for the JS to
                    # fire GetShoppingResults and receive results.
                    await _page.wait_for_timeout(8_000)
                    # If no results yet, wait a bit more (Google sometimes batches)
                    if not _collected:
                        await _page.wait_for_timeout(5_000)
                    await _ctx.close()
                return _collected

            # Handle already-running event loops (e.g. Jupyter / async callers)
            try:
                _loop = _asyncio.get_event_loop()
                if _loop.is_running():
                    import concurrent.futures as _cf5
                    with _cf5.ThreadPoolExecutor(max_workers=1) as _ex5:
                        _pw_opts = _ex5.submit(_asyncio.run, _pw_fetch()).result(timeout=75)
                else:
                    _pw_opts = _asyncio.run(_pw_fetch())
            except Exception:
                _pw_opts = []

            for _f in _pw_opts:
                _f.pop("_token", None)
                _f["source"] = "gf_search"
                _f["price"] = f"{currency} {_f['price']}" if isinstance(_f.get("price"), int) else _f.get("price", "")
                _key = (
                    tuple(_f["airlines"]),
                    _f["segments"][0].get("departure", "") if _f["segments"] else "",
                )
                if _key not in seen:
                    seen.add(_key)
                    merged.append(_f)
        except Exception:
            pass

    # ── Stage 4: supplemental schedule fallback ───────────────────────────────
    # For routes absent from Google's SSR cache (e.g. brand-new services),
    # fall back to user-editable schedules.json.  No airline-specific code here.
    if not merged and return_date is None:
        from .supplemental import lookup as _supp_lookup
        for r in _supp_lookup(origin, destination, departure_date):
            key = (
                tuple(r["airlines"]),
                r["segments"][0].get("departure", "") if r["segments"] else "",
            )
            if key not in seen:
                seen.add(key)
                merged.append(r)

    # Filter by max stops if specified
    if max_stops is not None:
        merged = [f for f in merged if f["stops"] <= max_stops]

    # Sort by price ascending (empty price string sorts last)
    merged.sort(key=_price_key)
    return merged[:max_results]


def _make_client():
    from ._utils import _make_client as _mc
    return _mc()
