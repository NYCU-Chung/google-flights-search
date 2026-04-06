"""
gf_search.setup() — one-time Google session bootstrap.

Opens a visible browser window so the user can sign into Google.
After sign-in is detected, all cookies (including session-only cookies
that would otherwise vanish on restart) are serialised to
~/.flight_agent/session_cookies.json so Stage 5 can inject them into
every search automatically.

Usage:
    import gf_search
    gf_search.setup()

Or from the command line:
    python -m gf_search.setup
"""

from __future__ import annotations

import json
from pathlib import Path

_FLIGHT_AGENT_DIR = Path.home() / ".flight_agent"
_PW_PROFILE = str(_FLIGHT_AGENT_DIR / "playwright_profile")
_SESSION_FILE = str(_FLIGHT_AGENT_DIR / "session_cookies.json")

_SESSION_COOKIE_NAMES = frozenset({
    "SID", "SSID", "APISID", "SAPISID",
    "__Secure-1PSID", "__Secure-3PSID",
    "__Secure-1PSIDTS", "__Secure-1PSIDCC",
    "__Secure-3PSIDCC", "__Host-GAPS",
})


def _has_google_session(cookies: list[dict]) -> bool:
    return any(c["name"] in _SESSION_COOKIE_NAMES for c in cookies)


def setup(timeout_seconds: int = 180) -> bool:
    """
    Open a visible Chrome/Chromium window at accounts.google.com.
    Polls until a Google session cookie is detected (user signed in),
    then saves ALL cookies to ~/.flight_agent/session_cookies.json and
    closes the browser.

    Stage 5 (fetcher.py) reads session_cookies.json on every search run
    and injects the cookies into the Playwright context, so Google Flights
    returns full results even after the browser restarts.

    Returns True if a Google session was established, False on timeout.

    Requires: playwright  (pip install playwright && playwright install chromium)
    """
    import asyncio

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print(
            "playwright is not installed.  Run:\n"
            "  pip install playwright\n"
            "  playwright install chromium\n"
            "then call gf_search.setup() again."
        )
        return False

    _FLIGHT_AGENT_DIR.mkdir(parents=True, exist_ok=True)
    Path(_PW_PROFILE).mkdir(parents=True, exist_ok=True)

    result: dict = {"ok": False}

    async def _run() -> None:
        async with async_playwright() as pw:
            try:
                ctx = await pw.chromium.launch_persistent_context(
                    _PW_PROFILE,
                    channel="chrome",
                    headless=False,
                    timeout=60_000,
                    args=["--disable-blink-features=AutomationControlled"],
                )
            except Exception:
                ctx = await pw.chromium.launch_persistent_context(
                    _PW_PROFILE,
                    headless=False,
                    timeout=60_000,
                )

            page = ctx.pages[0] if ctx.pages else await ctx.new_page()

            # Check if already signed in (session_cookies.json exists and usable)
            init_cookies = await ctx.cookies("https://www.google.com")
            if _has_google_session(init_cookies):
                print("Google session already active — saving cookies.")
                _save_cookies(await ctx.cookies())
                await ctx.close()
                result["ok"] = True
                return

            await page.goto("https://accounts.google.com", timeout=30_000)

            print()
            print("=" * 60)
            print("gf_search one-time setup")
            print("=" * 60)
            print("A browser window has opened.")
            print("Sign into your Google account.")
            print(f"Will auto-detect sign-in (timeout: {timeout_seconds}s).")
            print("=" * 60)

            import asyncio as _aio
            elapsed = 0
            poll_interval = 3
            while elapsed < timeout_seconds:
                await _aio.sleep(poll_interval)
                elapsed += poll_interval
                cookies = await ctx.cookies("https://www.google.com")
                if _has_google_session(cookies):
                    result["ok"] = True
                    print(f"\nSign-in detected after {elapsed}s.")
                    break
                print(f"  Waiting... ({elapsed}/{timeout_seconds}s)", end="\r")

            if result["ok"]:
                # Navigate to www.google.com to trigger persistent cookie writes,
                # then save the full cookie jar (including session-only cookies).
                await page.goto("https://www.google.com", timeout=20_000)
                await _aio.sleep(2)
                all_cookies = await ctx.cookies()
                _save_cookies(all_cookies)

            await ctx.close()

        if result["ok"]:
            print(
                f"\nSetup complete!  {_count_session()} session cookie(s) saved.\n"
                "Future searches will use your Google session automatically."
            )
        else:
            print(
                f"\nSetup timed out after {timeout_seconds}s without detecting sign-in.\n"
                "Run gf_search.setup() again and complete the Google sign-in."
            )

    asyncio.run(_run())
    return result["ok"]


def _save_cookies(cookies: list[dict]) -> None:
    with open(_SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(cookies, f)
    try:
        import os
        os.chmod(_SESSION_FILE, 0o600)
    except OSError:
        pass  # Windows does not support Unix permissions


def _count_session() -> int:
    try:
        cookies = json.loads(Path(_SESSION_FILE).read_text(encoding="utf-8"))
        return sum(1 for c in cookies if c["name"] in _SESSION_COOKIE_NAMES)
    except Exception:
        return 0


def load_session_cookies() -> list[dict]:
    """
    Return cookies from session_cookies.json, or [] if the file is missing.
    Called by fetcher.py Stage 5 to seed the Playwright context.
    """
    try:
        return json.loads(Path(_SESSION_FILE).read_text(encoding="utf-8"))
    except Exception:
        return []


if __name__ == "__main__":
    import sys
    ok = setup()
    sys.exit(0 if ok else 1)
