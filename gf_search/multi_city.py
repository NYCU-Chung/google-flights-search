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

import copy
import json
import re

_GF_SEARCH_URL = "https://www.google.com/travel/flights/search"
_BATCHEXEC_URL = "https://www.google.com/_/FlightsFrontendUi/data/batchexecute"
_GET_SHOPPING_URL = (
    "https://www.google.com/_/FlightsFrontendUi/data/"
    "travel.frontend.flights.FlightsFrontendService/GetShoppingResults"
)
_BATCHEXEC_HDR = {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}

_SEAT_MAP: dict[str, int] = {
    "economy": 1,
    "premium-economy": 2,
    "premium economy": 2,
    "business": 3,
    "first": 4,
}


# ── primp client ──────────────────────────────────────────────────────────────────

def _make_client():
    """Create a primp Client with Chrome impersonation, falling back to random."""
    from primp import Client
    try:
        return Client(
            impersonate="chrome_133",
            impersonate_os="macos",
            referer=True,
            cookie_store=True,
            timeout=120,
        )
    except Exception:
        return Client(
            impersonate="random",
            referer=True,
            cookie_store=True,
            timeout=120,
        )


# ── segment parsing helpers ───────────────────────────────────────────────────────

def _fmt_date(x) -> str:
    if not x or len(x) < 3:
        return ""
    try:
        return f"{int(x[0])}-{int(x[1]):02d}-{int(x[2]):02d}"
    except (TypeError, ValueError):
        return ""


def _fmt_time(x) -> str:
    if not x:
        return "00:00"
    try:
        h = int(x[0]) if x[0] is not None else 0
        m = int(x[1]) if len(x) > 1 and x[1] is not None else 0
        return f"{h:02d}:{m:02d}"
    except (TypeError, ValueError):
        return "00:00"


def _decode_unicode_escapes(name: str) -> str:
    """Fix \\uXXXX Unicode escapes not decoded by rjsonc (e.g. \\u661f = 星)."""
    if isinstance(name, str) and "\\" in name:
        try:
            return re.sub(
                r'\\u([0-9a-fA-F]{4})',
                lambda m: chr(int(m.group(1), 16)),
                name,
            )
        except Exception:
            pass
    return name


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

            raw_names = flight[1] if isinstance(flight[1], list) else [flight[1]]
            airlines = [
                _decode_unicode_escapes(n) if isinstance(n, str) else n
                for n in raw_names
            ]

            raw_segs = flight[2]
            segs = []
            for s in raw_segs:
                if not isinstance(s, list):
                    continue
                segs.append({
                    "from":         s[3]  if len(s) > 3  else "",
                    "to":           s[6]  if len(s) > 6  else "",
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
    try:
        outer = json.loads(raw[4:].lstrip())
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
    orig_inner: list,
    at_token: str,
) -> list[dict]:
    """
    Issue a GetShoppingResults POST to retrieve combined itinerary (circuit fare) results.

    orig_inner must NOT be mutated — pass copy.deepcopy() externally before calling.
    """
    filters = [[], copy.deepcopy(orig_inner), 0, 0, 0, 2]
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
        .replace("null", "None")
        .replace("true", "True")
        .replace("false", "False")
    )
    try:
        orig_inner = eval(inner_raw)[1]  # noqa: S307 — Google's embedded JS structure
    except Exception:
        return []

    legs_in_req: list = orig_inner[13]
    if not legs_in_req or len(legs_in_req) < len(segments):
        # Google did not return the expected leg structure; bail out
        return []

    at_m = re.search(r"[\"']at[\"']\s*:\s*[\"']([^\"']+)[\"']", html)
    at_token = at_m.group(1) if at_m else ""

    # ── Step 3: batchexecute leg 0 (warm session; required for GSR) ──────────
    # Always run even if we proceed to GSR — GSR needs the session to be
    # initialised via batchexecute first.
    TOP_K = max(5, max_results)

    leg0_opts = _do_batch(client, orig_inner, legs_in_req, at_token, None, 0)

    # ── Step 4: GetShoppingResults (primary — combined / circuit itineraries) ─
    gsr_results: list[dict] = []
    seen_gsr: set = set()

    try:
        gsr_opts = _do_gsr(client, orig_inner, at_token)

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

    # ── Step 5: batchexecute fallback (independent per-leg pricing) ──────────
    # Always run so callers receive both GSR combined fares and per-leg options.
    batch_results: list[dict] = []
    seen_batch: set = set()

    if leg0_opts:
        paths = [
            {"legs": [o], "token": o["token"], "total": o["price"]}
            for o in sorted(leg0_opts, key=lambda x: x["price"])[:TOP_K]
        ]

        for leg_idx in range(1, len(segments)):
            next_paths = []
            for path in paths:
                leg_opts = _do_batch(
                    client, orig_inner, legs_in_req, at_token, path["token"], leg_idx
                )
                for o in sorted(leg_opts, key=lambda x: x["price"])[:TOP_K]:
                    next_paths.append({
                        "legs":  path["legs"] + [o],
                        "token": o["token"],
                        "total": path["total"] + o["price"],
                    })
            if not next_paths:
                paths = []
                break
            paths = sorted(next_paths, key=lambda x: x["total"])[:TOP_K]

        for path in sorted(paths, key=lambda x: x["total"])[:max_results]:
            all_segs2: list[dict] = []
            all_airlines2: list[str] = []
            total_stops = 0

            for leg in path["legs"]:
                all_segs2.extend(leg["segments"])
                all_airlines2.extend(leg["airlines"])
                total_stops += leg["stops"]

            route_key2 = tuple(f"{s['from']}->{s['to']}" for s in all_segs2)
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
