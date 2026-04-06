"""
Shared low-level helpers for gf_search.

Consolidates the protobuf encoding primitives, date/time formatters, and
the primp HTTP-client factory that were previously duplicated across
builder.py, parser.py, multi_city.py, and fetcher.py.
"""

from __future__ import annotations

# ── Protobuf encoding primitives ─────────────────────────────────────────────

def _varint(n: int) -> bytes:
    if n < 0:
        raise ValueError("varint does not support negative values")
    buf = []
    while n > 0x7F:
        buf.append((n & 0x7F) | 0x80)
        n >>= 7
    buf.append(n & 0x7F)
    return bytes(buf)


def _field_varint(field_no: int, value: int) -> bytes:
    return _varint((field_no << 3) | 0) + _varint(value)


def _field_len(field_no: int, data: bytes) -> bytes:
    return _varint((field_no << 3) | 2) + _varint(len(data)) + data


# ── Date / time formatting ────────────────────────────────────────────────────

def _fmt_time(time_list) -> str:
    if not time_list:
        return "00:00"
    try:
        h = int(time_list[0]) if time_list[0] is not None else 0
        m = int(time_list[1]) if len(time_list) > 1 and time_list[1] is not None else 0
        return f"{h:02d}:{m:02d}"
    except (TypeError, ValueError, IndexError):
        return "00:00"


def _fmt_date(date_list) -> str:
    if not date_list or len(date_list) < 3:
        return ""
    try:
        return f"{int(date_list[0])}-{int(date_list[1]):02d}-{int(date_list[2]):02d}"
    except (TypeError, ValueError):
        return ""


# ── primp HTTP-client factory ─────────────────────────────────────────────────

_FALLBACK_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def _make_client(timeout: int = 60):
    """
    Create a primp Client with Chrome TLS impersonation.

    Tries chrome_146 first (latest supported profile), then falls back to
    progressively older versions, and finally to the default profile with
    a Chrome-like User-Agent header.
    """
    from primp import Client
    for profile in ("chrome_146", "chrome_131", "chrome_130"):
        try:
            c = Client(impersonate=profile, referer=True, cookie_store=True, timeout=timeout)
            if c.impersonate == profile:
                return c
        except Exception:
            continue
    return Client(
        referer=True, cookie_store=True, timeout=timeout,
        headers={"User-Agent": _FALLBACK_USER_AGENT},
    )
