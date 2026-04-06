"""
gf_search/multi_city.py — multi-city flight search via Google Flights.

Flow:
  1. build_tfs_multi_city(segments) → tfs
  2. primp GET google.com/travel/flights/search?tfs=... → HTML
  3. Extract orig_inner and at_token from the HTML
  4. Extract legs_in_req (one entry per segment) from orig_inner[13]
  5. batchexecute leg 0（warm session; GSR requires this）
  6. GetShoppingResults POST → combined itinerary results（primary）
  7. If GSR empty → continue batchexecute chaining（fallback）
  8. Merge, deduplicate, and return
"""

from __future__ import annotations

import ast
import copy
import json
import re

from ._utils import _fmt_date, _fmt_time, _make_client as _make_client_base

_GF_SEARCH_URL = "https://www.google.com/travel/flights/search"
_BATCHEXEC_URL = "https://www.google.com/_/FlightsFrontendUi/data/batchexecute"
_GET_SHOPPING_URL = (
    "https://www.google.com/_/FlightsFrontendUi/data/"
    "travel.frontend.flights.FlightsFrontendService/GetShoppingResults"
)
_BATCHEXEC_HDR = {
    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}

_SEAT_MAP: dict[str, int] = {
    "economy": 1,
    "premium-economy": 2,
    "premium economy": 2,
    "business": 3,
    "first": 4,
}


def _make_client():
    return _make_client_base(timeout=120)


def _parse_flight_section(section: list) -> list[dict]:
    """
    Parse a single data[2][0] or data[3][0] flight section from a batchexecute response.

    Each item structure:
      item[0] = flight  (airline codes, segments, etc.)
      item[1] = price_info  ([...[price], token, ...])
    """
    opts = []
    for item in section:
        try:
            flight = item[0]
            price_info = item[1]
            price_raw = price_info[0][1]
            token = price_info[1]

            # booking_token: item[1][1] — present in GSR combined itinerary results
            booking_token = None
            try:
                booking_token = price_info[1] if price_info[1] else None
            except (IndexError, TypeError):
                pass

            raw_segs = flight[2]
            segs = []
            for s in raw_segs:
                if not isinstance(s, list):
                    continue
                seg_info = s[22] if len(s) > 22 and isinstance(s[22], list) else []
                flight_no = (
                    f"{seg_info[0]}{seg_info[1]}"
                    if len(seg_info) >= 2 and seg_info[0] and seg_info[1]
                    else ""
                )
                segs.append({
                    "from":         s[3]  if len(s) > 3  else "",
                    "to":           s[6]  if len(s) > 6  else "",
                    "flight_no":    flight_no,
                    "departure":    (
                        f"{_fmt_date(s[20] if len(s) > 20 else None)} "
                        f"{_fmt_time(s[8]  if len(s) > 8  else None)}"
                    ).strip(),
                    "arrival":      (
                        f"{_fmt_date(s[21] if len(s) > 21 else None)} "
                        f"{_fmt_time(s[10] if len(s) > 10 else None)}"
                    ).strip(),
                    "duration_min": s[11] if len(s) > 11 else 0,
                    "plane":        s[17] if len(s) > 17 else "",
                })

            # Use IATA carrier codes from segment data (consistent with parser.py)
            try:
                seg_carriers = [
                    s[22][0]
                    for s in raw_segs
                    if isinstance(s, list) and len(s) > 22
                    and isinstance(s[22], list) and s[22] and s[22][0]
                ]
                airlines = list(dict.fromkeys(seg_carriers)) if seg_carriers else [flight[0]]
            except (IndexError, TypeError):
                airlines = [flight[0]] if flight[0] else []

            opts.append({
                "airlines":      airlines,
                "price":         price_raw,
                "stops":         max(0, len(segs) - 1),
                "segments":      segs,
                "token":         token,
                "booking_token": booking_token,
            })
        except (IndexError, TypeError, KeyError):
            continue
    return opts


# ── batchexecute helper ───────────────────────────────────────────────────────────

def _parse_batch_response(raw: str) -> list[dict]:
    """
    Parse the raw text returned by a batchexecute POST.

    The response starts with )]}'\\n followed by a JSON array.
    We look for the entry where e[0] == 'wrb.fr' and e[2] is non-empty,
    then parse the inner JSON to extract flight options.
    """
    if not raw.startswith(")]}'"):
        return []
    stripped = raw[4:].lstrip()
    # GetShoppingResults uses a streaming format with a chunk-length prefix:
    #   )]}'\\n\\nNUM\\n[[...  — strip the NUM\\n before the JSON array.
    # batchexecute responses have no such prefix; the regex is a no-op for them.
    _len_m = re.match(r'^(\d+)\n(.*)', stripped, re.DOTALL)
    if _len_m:
        chunk_len = int(_len_m.group(1))
        remaining = _len_m.group(2)
        if chunk_len <= len(remaining):
            stripped = remaining
    # Use raw_decode so trailing data / extra streaming frames are tolerated.
    try:
        outer, _ = json.JSONDecoder().raw_decode(stripped)
    except (json.JSONDecodeError, ValueError):
        return []

    entry = next(
        (
            e for e in outer
            if isinstance(e, list) and len(e) >= 3 and e[0] == "wrb.fr" and e[2]
        ),
        None,
    )
    if not entry:
        return []

    try:
        cd = json.loads(entry[2])
    except (json.JSONDecodeError, ValueError, TypeError):
        return []

    opts = []
    for sec_idx in [2, 3]:
        if (
            len(cd) > sec_idx
            and isinstance(cd[sec_idx], list)
            and cd[sec_idx]
            and isinstance(cd[sec_idx][0], list)
        ):
            opts.extend(_parse_flight_section(cd[sec_idx][0]))
    return opts


def _do_batch(
    client,
    orig_inner: list,
    legs_in_req: list,
    at_token: str,
    prev_token,    # None for first leg; str token for subsequent legs
    leg_idx: int,
) -> list[dict]:
    """
    Issue one batchexecute call for the given leg.

    legs_in_req is passed explicitly to avoid closure surprises.
    prev_token is None when querying leg 0 (no prior selection).
    """
    inner = copy.deepcopy(orig_inner)
    inner[13] = [legs_in_req[leg_idx]]
    req = [[], inner] if prev_token is None else [[[prev_token]], inner]

    post_data = {
        "f.req": json.dumps([[["LqxFAb", json.dumps(req), None, "generic"]]])
    }
    if at_token:
        post_data["at"] = at_token

    try:
        r = client.post(_BATCHEXEC_URL, data=post_data, headers=_BATCHEXEC_HDR)
    except Exception:
        return []

    return _parse_batch_response(r.text) if r.status_code == 200 else []


def _do_gsr(
    client,
    segments: list[dict],
    seat: int,
    adults: int,
    at_token: str,
) -> list[dict]:
    """
    Issue a GetShoppingResults POST using the simplified JSON format observed in
    Chrome on 2026-04-01.

    The browser sends a compact query structure with IATA codes directly — NOT the
    full orig_inner protobuf.  This format works without a Google session for major
    airports; small/low-traffic airports still require a valid session cookie.
    """
    from .builder import CITY_ENTITIES

    def _gsr_airport(iata: str) -> list:
        """Return [[[entity_id, entity_type]]] for a GSR segment endpoint.
        City/metro entities use entity_type 2 + numeric ID; plain airports use 0.
        (Browser sends entity_type 0 for standard airports; 2 for city entities.)
        """
        iata = iata.upper()
        if iata in CITY_ENTITIES:
            return [[[CITY_ENTITIES[iata], 2]]]
        return [[[iata, 0]]]

    seg_list = []
    for seg in segments:
        seg_list.append([
            _gsr_airport(seg["from"]),        # origins: [[[entity_id, entity_type]]]
            _gsr_airport(seg["to"]),          # destinations
            None, 0, None, None,
            seg["date"],                      # YYYY-MM-DD
            None, None, None, None, None, None, None,
            3,    # max-stops filter (browser always sends 3 = any)
        ])

    inner = [
        None, None, 1, None, [],
        adults,              # [5]: adult count
        [adults, 0, 0, 0],  # [6]: [adults, children, infants_lap, infants_seat]
        None, None, None, None, None, None,
        seg_list,            # [13]: route segments
        None, None, None,
        seat,                # [17]: cabin class (1=economy … 4=first)
    ]
    filters = [[], inner, 0, 0, 0, 3]   # outer[5]=3 → multi-city trip type
    filters_json = json.dumps(filters, separators=(",", ":"))
    wrapped = json.dumps([None, filters_json], separators=(",", ":"))
    gsr_pd: dict = {"f.req": wrapped}
    if at_token:
        gsr_pd["at"] = at_token

    try:
        r = client.post(_GET_SHOPPING_URL, data=gsr_pd, headers=_BATCHEXEC_HDR)
    except Exception:
        return []

    return _parse_batch_response(r.text) if r.status_code == 200 else []


# ── main public function ──────────────────────────────────────────────────────────

def search_multi_city(
    segments: list[dict],       # [{"from": "TPE", "to": "NRT", "date": "2026-05-01"}, ...]
    adults: int = 1,
    travel_class: str = "economy",
    max_results: int = 5,
    exclude_budget: bool = False,
) -> list[dict]:
    """
    Search multi-city itineraries via Google Flights.

    Primary path: GetShoppingResults (GSR) — returns combined itineraries
    (circuit fares / single PNR where available).
    Fallback path: batchexecute token chaining — returns per-leg pricing aggregated.

    Does NOT depend on fast-flights or flights_legacy.

    Parameters
    ----------
    segments : list[dict]
        Each dict must have keys "from", "to", "date" (YYYY-MM-DD).
    adults : int
        Number of adult passengers.
    travel_class : str
        One of: economy, premium-economy, business, first.
    max_results : int
        Maximum number of results to return.
    exclude_budget : bool
        If True, exclude results from budget airlines (placeholder; filtering
        can be added by the caller based on the "airlines" field).

    Returns
    -------
    list[dict]
        Each dict has keys: airlines, price, stops, segments, source, note.
        GSR results also include booking_token when available.
        Returns [] on any unrecoverable error.
    """
    try:
        from primp import Client  # noqa: F401 (just to check availability)
    except ImportError:
        return []

    from .builder import build_tfs_multi_city

    seat_no = _SEAT_MAP.get(travel_class.lower(), 1)

    # ── Step 1: Build tfs and fetch initial HTML ──────────────────────────────
    tfs = build_tfs_multi_city(segments, seat=seat_no, adults=adults)

    client = _make_client()
    params = {"tfs": tfs, "tfu": "EgIIACIA", "hl": "zh-TW"}
    headers = {"Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"}

    try:
        res = client.get(_GF_SEARCH_URL, params=params, headers=headers)
    except Exception:
        return []

    if res.status_code != 200:
        return []

    html = res.text

    # ── Step 2: Extract orig_inner and at_token ───────────────────────────────
    # orig_inner is the request structure embedded in the page that Google uses
    # for subsequent batchexecute calls.  It is JavaScript, not JSON, so we
    # use eval() after replacing JS literals with Python equivalents.
    # This is a known risk (noqa: S307) — the data comes from Google's own page.
    af_m = re.search(
        r"'ds:1'\s*:\s*\{id:'LqxFAb',request:(\[.+?\])\}",
        html,
        re.DOTALL,
    )
    if not af_m:
        return []

    inner_raw = (
        af_m.group(1)
        .replace("undefined", "None")
        .replace("null", "None")
        .replace("true", "True")
        .replace("false", "False")
    )
    try:
        orig_inner = ast.literal_eval(inner_raw)[1]
    except (ValueError, SyntaxError):
        return []
    except Exception:
        return []

    legs_in_req: list = orig_inner[13]
    if not legs_in_req or len(legs_in_req) < len(segments):
        # Google did not return the expected leg structure; bail out
        return []

    at_m = re.search(r'"SNlM0e":"([^"]+)"', html)
    at_token = at_m.group(1) if at_m else ""

    # ── Step 3: batchexecute leg 0 (warm session; required for GSR) ──────────
    # Always run even if we proceed to GSR — GSR needs the session to be
    # initialised via batchexecute first.
    leg0_opts = _do_batch(client, orig_inner, legs_in_req, at_token, None, 0)

    # ── Step 4: GetShoppingResults (primary — combined / circuit itineraries) ─
    gsr_results: list[dict] = []
    seen_gsr: set = set()

    try:
        gsr_opts = _do_gsr(client, segments, seat_no, adults, at_token)

        for opt in sorted(gsr_opts, key=lambda x: x["price"]):
            if len(gsr_results) >= max_results:
                break

            all_segs = list(opt["segments"])

            # GSR only returns segments for the first leg in detail.
            # Fill in route stubs for any uncovered legs using the segment params.
            if all_segs:
                last_to = all_segs[-1]["to"]
                covered = 0
                for i, seg_def in enumerate(segments):
                    if seg_def["to"].upper() == last_to.upper():
                        covered = i + 1
                        break
                if covered == 0:
                    covered = min(len(all_segs), len(segments))
            else:
                covered = 0

            for seg_def in segments[covered:]:
                all_segs.append({
                    "from":         seg_def["from"].upper(),
                    "to":           seg_def["to"].upper(),
                    "departure":    seg_def["date"],
                    "arrival":      "",
                    "duration_min": 0,
                    "plane":        "",
                })

            route_key = tuple(f"{s['from']}->{s['to']}" for s in all_segs)
            fp = (opt["price"], route_key)
            if fp in seen_gsr:
                continue
            seen_gsr.add(fp)

            connections = max(0, len(all_segs) - len(segments))
            entry: dict = {
                "airlines": opt["airlines"],
                "price":    f"TWD {opt['price']}",
                "stops":    connections,
                "segments": all_segs,
                "source":   "gf_search_multi_city_gsr",
                "note":     "Google Flights 聯票（可能為環程票，單一 PNR）",
            }
            if opt.get("booking_token"):
                entry["booking_token"] = opt["booking_token"]
            gsr_results.append(entry)
    except Exception:
        pass

    # ── Step 4b: Enrich GSR results with full segment details ────────────────
    # GSR only returns flight details for the first leg. We match each GSR
    # result's first-leg flight_no to a leg0_opts token, then chain
    # batchexecute for the remaining legs to recover actual flight numbers.
    # The GSR combined price is kept as-is; segments are filled in best-effort.
    if gsr_results and leg0_opts and len(segments) > 1:
        leg0_by_fno: dict[str, dict] = {}
        for _opt in leg0_opts:
            if _opt.get("segments"):
                _fno = _opt["segments"][0].get("flight_no", "")
                if _fno and _fno not in leg0_by_fno:
                    leg0_by_fno[_fno] = _opt

        for _gr in gsr_results:
            if not _gr["segments"]:
                continue
            _first_fno = _gr["segments"][0].get("flight_no", "")
            _matched = leg0_by_fno.get(_first_fno)
            if not _matched:
                continue

            # Chain batchexecute for legs 1..N-1 starting from matched token
            _chain_segs: list[dict] = list(_matched["segments"])
            _chain_airlines: list[str] = list(_matched["airlines"])
            _tok = _matched["token"]
            _ok = True
            for _li in range(1, len(segments)):
                _lopts = _do_batch(client, orig_inner, legs_in_req, at_token, _tok, _li)
                if not _lopts:
                    _ok = False
                    break
                _best = sorted(_lopts, key=lambda x: x["price"])[0]
                _chain_segs.extend(_best["segments"])
                _chain_airlines.extend(_best["airlines"])
                _tok = _best["token"]

            if _ok and len(_chain_segs) >= len(segments):
                _gr["segments"] = _chain_segs
                _gr["airlines"] = list(dict.fromkeys(_chain_airlines))
                _gr["stops"] = max(0, len(_chain_segs) - len(segments))
                _gr["note"] = "Google Flights 聯票（聯票定價，航班明細由 batchexecute 補全）"

    # ── Step 5: batchexecute fallback (independent per-leg pricing) ──────────
    # Always run so callers receive both GSR combined fares and per-leg options.
    batch_results: list[dict] = []
    seen_batch: set = set()

    # Fixed chain width — independent of max_results to avoid Google rate limiting.
    # 30 paths × 3 legs = 90 batchexecute calls (vs 150*3=450 with scaled TOP_K).
    _CHAIN_K = 30

    if leg0_opts:
        # Diversity pruning at leg 0: keep cheapest option per unique first-airline
        # so FSC carriers (CI, BR, JL) always get a representative path even when
        # dozens of cheaper LCC (GK, UO) options exist.
        from collections import defaultdict as _dd
        _leg0_by_al: dict = _dd(list)
        for _o in leg0_opts:
            _al = (_o.get("airlines") or ["?"])[0]
            _leg0_by_al[_al].append(_o)
        # One cheapest per airline, then fill up to _CHAIN_K with cheapest overall
        _leg0_diverse: list[dict] = [
            sorted(_v, key=lambda x: x["price"])[0]
            for _v in _leg0_by_al.values()
        ]
        _leg0_diverse_ids = {id(_o) for _o in _leg0_diverse}
        for _o in sorted(leg0_opts, key=lambda x: x["price"]):
            if len(_leg0_diverse) >= _CHAIN_K:
                break
            if id(_o) not in _leg0_diverse_ids:
                _leg0_diverse.append(_o)
                _leg0_diverse_ids.add(id(_o))

        paths = [
            {"legs": [o], "token": o["token"], "total": o["price"]}
            for o in _leg0_diverse[:_CHAIN_K]
        ]

        for leg_idx in range(1, len(segments)):
            next_paths = []
            for path in paths:
                leg_opts = _do_batch(
                    client, orig_inner, legs_in_req, at_token, path["token"], leg_idx
                )
                # Per-airline diversity within each leg so FSC carriers (CI, BR, JL)
                # are always represented even when cheaper LCC options dominate top-K.
                _by_leg_al: dict = {}
                for _o in leg_opts:
                    _al = (_o.get("airlines") or ["?"])[0]
                    if _al not in _by_leg_al or _o["price"] < _by_leg_al[_al]["price"]:
                        _by_leg_al[_al] = _o
                _leg_diverse: list[dict] = list(_by_leg_al.values())
                _leg_seen_ids = {id(_o) for _o in _leg_diverse}
                for _o in sorted(leg_opts, key=lambda x: x["price"]):
                    if len(_leg_diverse) >= _CHAIN_K:
                        break
                    if id(_o) not in _leg_seen_ids:
                        _leg_diverse.append(_o)
                        _leg_seen_ids.add(id(_o))
                for o in _leg_diverse[:_CHAIN_K]:
                    next_paths.append({
                        "legs":  path["legs"] + [o],
                        "token": o["token"],
                        "total": path["total"] + o["price"],
                    })
            if not next_paths:
                paths = []
                break
            # Diversity-aware pruning: keep cheapest path per unique airline combo
            # (tuple of first airline per leg) so CI+CI+CI+CI survives alongside
            # GK+UO+UO+GK without being pruned by price dominance.
            _by_combo: dict = _dd(list)
            for _p in next_paths:
                _combo = tuple(
                    (_l.get("airlines") or ["?"])[0] for _l in _p["legs"]
                )
                _by_combo[_combo].append(_p)
            _diverse = [
                sorted(_v, key=lambda x: x["total"])[0]
                for _v in _by_combo.values()
            ]
            paths = sorted(_diverse, key=lambda x: x["total"])[:_CHAIN_K]

        # ── Step 5b: Targeted FSC chains ──────────────────────────────────
        # For each full-service airline found at leg 0, build a dedicated chain
        # that always prefers the same airline at each subsequent leg.
        # This finds CI+CI+CI+CI, BR+BR+BR+BR etc. with only
        # (n_fsc_airlines × n_legs) extra batchexecute calls instead of
        # the exponential fan-out required by price-ranked combo expansion.
        _MAX_FSC_CHAINS = 5
        _budget_set_local = {"UO", "IT", "MM", "GK", "TR", "3K", "VZ", "FD", "SL", "HB"}
        _fsc_leg0 = [
            o for o in leg0_opts
            if (o.get("airlines") or ["?"])[0] not in _budget_set_local
        ]
        # Keep only the cheapest option per FSC airline to cap chain count
        _fsc_seen_al: set[str] = set()
        _fsc_leg0_dedup: list[dict] = []
        for _o in sorted(_fsc_leg0, key=lambda x: x["price"]):
            _al = (_o.get("airlines") or ["?"])[0]
            if _al not in _fsc_seen_al:
                _fsc_seen_al.add(_al)
                _fsc_leg0_dedup.append(_o)
            if len(_fsc_leg0_dedup) >= _MAX_FSC_CHAINS:
                break
        for _fsc0 in _fsc_leg0_dedup:
            _target_al = (_fsc0.get("airlines") or ["?"])[0]
            _fsc_legs = [_fsc0]
            _fsc_tok = _fsc0["token"]
            _fsc_total = _fsc0["price"]
            _ok = True
            for _li in range(1, len(segments)):
                _lopts = _do_batch(
                    client, orig_inner, legs_in_req, at_token, _fsc_tok, _li
                )
                if not _lopts:
                    _ok = False
                    break
                # Prefer same airline; else cheapest FSC; else cheapest overall
                _same_al = [
                    o for o in _lopts
                    if (o.get("airlines") or ["?"])[0] == _target_al
                ]
                if _same_al:
                    _best_fsc = sorted(_same_al, key=lambda x: x["price"])[0]
                else:
                    _fsc_alts = [
                        o for o in _lopts
                        if (o.get("airlines") or ["?"])[0] not in _budget_set_local
                    ]
                    _best_fsc = sorted(
                        _fsc_alts or _lopts, key=lambda x: x["price"]
                    )[0]
                _fsc_legs.append(_best_fsc)
                _fsc_tok = _best_fsc["token"]
                _fsc_total += _best_fsc["price"]
            if _ok and len(_fsc_legs) == len(segments):
                paths.append({
                    "legs":  _fsc_legs,
                    "token": _fsc_tok,
                    "total": _fsc_total,
                })

        # Re-dedup paths by combo after appending FSC chains
        _paths_by_combo: dict = {}
        for _p in paths:
            _c = tuple((_l.get("airlines") or ["?"])[0] for _l in _p["legs"])
            if _c not in _paths_by_combo or _p["total"] < _paths_by_combo[_c]["total"]:
                _paths_by_combo[_c] = _p
        paths = sorted(_paths_by_combo.values(), key=lambda x: x["total"])

        for path in sorted(paths, key=lambda x: x["total"])[:max_results * 3]:
            all_segs2: list[dict] = []
            all_airlines2: list[str] = []
            total_stops = 0

            for leg in path["legs"]:
                all_segs2.extend(leg["segments"])
                all_airlines2.extend(leg["airlines"])
                total_stops += leg["stops"]

            # Include flight_no in route key so different airline combos with same
            # total price are not collapsed (e.g. GK+UO vs CI+UO same price edge case)
            route_key2 = tuple(
                f"{s['from']}->{s['to']}@{s.get('flight_no','')}"
                for s in all_segs2
            )
            fp2 = (path["total"], route_key2)
            if fp2 in seen_batch:
                continue
            seen_batch.add(fp2)

            batch_results.append({
                "airlines": list(dict.fromkeys(all_airlines2)),
                "price":    f"TWD {path['total']}",
                "stops":    total_stops,
                "segments": all_segs2,
                "source":   "gf_search_multi_city",
                "note":     "各段 Google Flights 最低票加總（分段獨立票，非聯票）",
            })

    # ── Step 6: Merge and return (GSR first, then batchexecute) ──────────────
    # GSR combined fares take priority; batchexecute per-leg options follow.
    # Deduplicate across both sources by (price_str, route_key).
    combined: list[dict] = []
    seen_combined: set = set()

    for result in gsr_results + batch_results:
        route_key_c = tuple(f"{s['from']}->{s['to']}" for s in result["segments"])
        fp_c = (result["price"], route_key_c)
        if fp_c in seen_combined:
            continue
        seen_combined.add(fp_c)
        combined.append(result)
        if len(combined) >= max_results * 2:  # keep a generous pool for the caller
            break

    return combined[:max_results * 2] if combined else []
