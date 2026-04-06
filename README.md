# Google Flights Search

[繁體中文](README_zh.md) | English

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

A lightweight Google Flights client that **actually works for small and regional airports**. Uses a fast SSR path for popular routes; falls back to a Playwright-based stage for regional airports (e.g. Taichung RMQ, Kumamoto KMJ).

## Why gf-search instead of fast-flights?

Existing libraries like `fast-flights` silently return empty results for low-traffic airports (e.g. Taichung RMQ, Kumamoto KMJ). The root cause is an **incomplete protobuf URL encoding**: Google receives a malformed request and skips on-demand calculation, returning `data[3] = null`.

`gf-search` reverse-engineered the exact protobuf format Chrome sends, with three critical fixes:

| Field | fast-flights | gf-search |
|-------|-------------|-----------|
| `Airport.field_1` (entity type) | missing | `1` = IATA airport, `2` = city entity ID |
| `Info.field_1`, `Info.field_2` | missing | `28`, `2` (query type flags) |
| `Info.field_16` | missing | `INT64_MAX` — triggers on-demand calculation for small airports |

**Result:** `RMQ → KMJ` returns full flight data including Starlux Airlines (JX) direct flights, whereas fast-flights returns `data[3] = null`.

## Installation

### Basic (SSR only — works for most popular routes)

```bash
pip install google-flights-search
```

### + Playwright fallback (required for small/regional airports like RMQ, KMJ)

```bash
pip install "google-flights-search[playwright]"
playwright install chromium      # download browser binary (~130 MB), one-time
gf-search-setup                  # one-time Google sign-in → saves session
```

`gf-search-setup` opens a browser window. Sign into your Google account; the session is auto-detected and saved to `~/.flight_agent/session_cookies.json`. All subsequent searches use it automatically — no further setup needed.

### Windows: automatic Chrome session extraction

When Chrome is **not running**, the session can be extracted directly from Chrome's cookie store (no manual sign-in needed):

```bash
pip install "google-flights-search[playwright,windows]"
playwright install chromium
```

### All features

```bash
pip install "google-flights-search[full]"
playwright install chromium
gf-search-setup
```

### Local / editable development

```bash
git clone https://github.com/NYCU-Chung/google-flights-search
cd google-flights-search
pip install -e ".[playwright,windows]"
playwright install chromium
gf-search-setup
```

## Quick Start

```python
from gf_search import search

# Search flights from Taoyuan (TPE) to Tokyo Narita (NRT)
results = search("TPE", "NRT", "2026-08-08")
for r in results:
    print(r["airlines"], r["price"], r["stops"], "stop(s)")
```

```python
# Small airport example — this is where gf-search shines
# fast-flights returns nothing; gf-search returns Starlux JX direct flights
results = search("RMQ", "KMJ", "2026-08-08")
for r in results:
    print(r["airlines"], r["price"])
```

## API Reference

### `search()`

```python
from gf_search import search

results = search(
    origin="TPE",           # IATA departure airport code
    destination="NRT",      # IATA arrival airport code
    departure_date="2026-08-08",   # "YYYY-MM-DD"
    return_date=None,       # "YYYY-MM-DD" for round-trip; None for one-way
    adults=1,               # number of adult passengers
    travel_class="economy", # "economy" | "premium-economy" | "business" | "first"
    max_results=5,          # maximum number of results to return
    currency="TWD",         # price currency code (e.g. "TWD", "USD", "JPY")
    max_stops=None,         # max layovers: 0=direct only, 1=max 1 stop, None=any
)
```

**Returns:** `list[dict]`, each dict has the shape:

```python
{
    "airlines": ["JX"],                      # IATA carrier code(s) — one per operating carrier
    "price": "TWD 8900",                     # price string, or "" if unavailable
    "stops": 0,                              # number of layovers
    "segments": [
        {
            "from": "RMQ",
            "to": "KMJ",
            "flight_no": "JX317",            # carrier + flight number (e.g. "JX317", "CI002")
            "departure": "2026-08-08 15:00",
            "arrival": "2026-08-08 18:15",
            "duration_min": 95,
            "plane": "Airbus A321neo",
        }
    ],
    "source": "gf_search",
}
```

Returns `[]` if no results are found after retries.

---

### `session_status()`

Check the health of the saved Google session (used by Stage 5 Playwright fallback).

```python
from gf_search import session_status

status = session_status()
print(status)
# {
#     "valid": True,           # True if session exists and is fresh
#     "exists": True,          # True if session_cookies.json exists
#     "age_hours": 72.5,       # hours since last setup (or -1)
#     "stale": False,          # True if session is older than 72h
#     "cookie_count": 10,      # number of session cookies
#     "message": "Google session 有效（10 cookies，72 小時前更新）。"
# }
```

---

### `build_tfs()`

Builds the raw `tfs` URL parameter (URL-safe base64-encoded protobuf) for the Google Flights search endpoint. Useful if you want to construct URLs manually or inspect the encoding.

```python
from gf_search import build_tfs

tfs = build_tfs(
    origin="RMQ",
    destination="KMJ",
    departure_date="2026-08-08",
    return_date="2026-08-15",   # optional
    seat=1,                     # 1=economy 2=premium-economy 3=business 4=first
    adults=1,
)

url = f"https://www.google.com/travel/flights/search?tfs={tfs}&tfu=EgIIACIA&hl=zh-TW"
print(url)
```

---

### `CITY_ENTITIES`

A dict mapping IATA codes to Google's city/metro entity IDs. Regular airports use `entity_type=1` (handled automatically). Airports that Google indexes at the city level need `entity_type=2` with a special entity ID.

```python
from gf_search import CITY_ENTITIES

print(CITY_ENTITIES)
# {
#     "RMQ": "/m/01r8pt",   # Taichung (city entity)
#     "KHH": "/m/0h7h6",    # Kaohsiung
#     "TSA": "/m/02kg86",   # Taipei Songshan
# }

# Add your own:
CITY_ENTITIES["OKA"] = "/m/0h7r_"  # Okinawa Naha
```

To find an entity ID: open Google Flights in Chrome DevTools, trigger a search for the target airport, and inspect the `tfs` parameter in the network request.

---

## MCP Server (for Claude and AI assistants)

`gf-search` ships a built-in MCP server. Once published to PyPI, anyone can add it to Claude Desktop with a single config entry — no pre-installation required.

**Claude Desktop config** (`%APPDATA%\Claude\claude_desktop_config.json` on Windows, `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "google-flights": {
      "command": "uvx",
      "args": ["--from", "google-flights-search", "gf-search-mcp"]
    }
  }
}
```

Restart Claude Desktop. Claude will have access to four tools:

- **`search_flights`** — single origin-destination search (supports `max_stops` filter; returns helpful hints when results are empty or priceless)
- **`search_multi_city_flights`** — multi-city / open-jaw / 4-leg itineraries
- **`search_cheapest_dates`** — search a date range and return results sorted by cheapest price (great for flexible-date travellers)
- **`generate_search_urls`** — generate a Google Flights URL that can be opened in a browser (useful when API results are incomplete for niche routes)

If you prefer installing manually first:

```bash
pip install "google-flights-search[mcp]"
```

```json
{
  "mcpServers": {
    "google-flights": {
      "command": "gf-search-mcp"
    }
  }
}
```

---

## How It Works

`gf-search` uses a multi-stage pipeline, stopping as soon as results are found:

| Stage | Method | Requires |
|-------|--------|----------|
| 0 | Chrome-authenticated cache (`~/.gf_search/chrome_cache.json`) | Pre-populated cache file |
| 1–3 | `primp` SSR + `tfu`/`batchexecute` fallbacks | Nothing (pure HTTP) |
| 5 | Playwright: real Chrome/Chromium, network interception | `playwright` + `gf-search-setup` |
| 4 | Supplemental schedules (`schedules.json`) | Nothing |

**Stages 1–3 (fast path):** Google Flights renders flight data server-side into a `<script class="ds:1">` tag. `gf-search`:

1. Builds a correctly-encoded protobuf `tfs` parameter — three fields missing from other libraries are the key fix
2. Fetches via `primp` (Rust HTTP client that impersonates Chrome's TLS fingerprint)
3. Retries up to 3×; if still empty, tries a `tfu`-based return-leg fetch and a `batchexecute` chain

**Stage 5 (regional airports & round-trips):** For routes where Google's SSR cache is empty (e.g. RMQ→KMJ), or when all SSR results lack prices, a real Chrome/Chromium session is launched via Playwright. For round-trip queries, Stage 5 first attempts the native round-trip URL to preserve round-trip pricing; if Playwright fails, the search is decomposed into two one-way legs (results include a `direction` field and `_price_note`). Network responses (`GetShoppingResults`) are intercepted and parsed directly — no airline-specific code, works for any route Google has indexed. Session cookies from `session_cookies.json` are automatically injected into both Playwright and SSR requests.

---

## Limitations

- **Non-official API:** Google may change the response format at any time.
- **SSR non-determinism:** Even with the correct protobuf, flight data sections are occasionally `null` on a cold cache hit. The built-in 3-retry logic handles most cases.
- **Regional airports need Playwright:** Routes where Google's SSR cache is empty (small airports) require `pip install "google-flights-search[playwright]"` + `playwright install chromium` + `gf-search-setup`.
- **Google session:** Stage 5 works without a session but returns fewer results. Run `gf-search-setup` once for full coverage. Setup mainly affects niche routes (Stage 5); popular routes return the same results with or without a session via SSR.
- **Price currency:** Configurable via the `currency` parameter (default `"TWD"`).
- **No seat map / availability API:** Search results only; no booking-level availability.

---

## Contributing

PRs are welcome! The most impactful contributions right now:

- **More city entity IDs** in `CITY_ENTITIES` (any airport where Google uses a city-level entity rather than an IATA code directly)
- Expanded `_SEAT_MAP` aliases

To add a city entity ID, find it via Chrome DevTools as described above, then add it to `gf_search/builder.py`.

---

## License

MIT — see [LICENSE](LICENSE).
