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
import time
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

# Session cookies older than this are considered stale.
_SESSION_MAX_AGE_HOURS = 72


def _has_google_session(cookies: list[dict]) -> bool:
    return any(c["name"] in _SESSION_COOKIE_NAMES for c in cookies)


def session_status() -> dict:
    """
    Check the current session health.

    Returns a dict:
        {
            "valid": bool,          # True if session exists and is fresh
            "exists": bool,         # True if session_cookies.json exists
            "age_hours": float,     # Hours since last setup (or -1)
            "stale": bool,          # True if session is older than 72h
            "cookie_count": int,    # Number of session cookies
            "message": str,         # Human-readable status message
        }
    """
    import os

    result = {
        "valid": False, "exists": False,
        "age_hours": -1, "stale": False,
        "cookie_count": 0, "message": "",
    }

    if not os.path.isfile(_SESSION_FILE):
        result["message"] = "未設定 Google session。執行 gf_search.setup() 可提升冷門路線的搜尋品質。"
        return result

    result["exists"] = True
    try:
        mtime = os.path.getmtime(_SESSION_FILE)
        age_hours = (time.time() - mtime) / 3600
        result["age_hours"] = round(age_hours, 1)

        cookies = json.loads(Path(_SESSION_FILE).read_text(encoding="utf-8"))
        session_count = sum(1 for c in cookies if c.get("name") in _SESSION_COOKIE_NAMES)
        result["cookie_count"] = session_count

        if session_count == 0:
            result["message"] = "session_cookies.json 存在但不含有效的 Google session cookies。請重新執行 gf_search.setup()。"
            return result

        if age_hours > _SESSION_MAX_AGE_HOURS:
            result["stale"] = True
            result["message"] = f"Google session 已 {age_hours:.0f} 小時未更新（建議每 {_SESSION_MAX_AGE_HOURS} 小時刷新）。執行 gf_search.setup() 更新。"
            return result

        result["valid"] = True
        result["message"] = f"Google session 有效（{session_count} cookies，{age_hours:.0f} 小時前更新）。"
        return result
    except Exception as e:
        result["message"] = f"無法讀取 session 狀態：{e}"
        return result


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
            "❌ playwright 未安裝。請執行：\n"
            "  pip install playwright\n"
            "  playwright install chromium\n"
            "然後再次執行 gf_search.setup()。"
        )
        return False

    _FLIGHT_AGENT_DIR.mkdir(parents=True, exist_ok=True)
    Path(_PW_PROFILE).mkdir(parents=True, exist_ok=True)

    # Check if session is already valid
    status = session_status()
    if status["valid"]:
        print(f"✅ {status['message']}")
        print("如果搜尋結果不完整，可以強制重新登入：gf_search.setup()")
        return True

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

            # Check if already signed in via persistent profile
            init_cookies = await ctx.cookies("https://www.google.com")
            if _has_google_session(init_cookies):
                print("✅ 偵測到已登入的 Google session，正在儲存...")
                _save_cookies(await ctx.cookies())
                await ctx.close()
                result["ok"] = True
                return

            await page.goto("https://accounts.google.com", timeout=30_000)

            print()
            print("┌──────────────────────────────────────────┐")
            print("│  gf-search 一次性設定                     │")
            print("├──────────────────────────────────────────┤")
            print("│  瀏覽器視窗已開啟。                        │")
            print("│  請登入你的 Google 帳號。                  │")
            print("│  登入完成後會自動偵測並關閉。                │")
            print(f"│  超時：{timeout_seconds} 秒                              │")
            print("└──────────────────────────────────────────┘")

            import asyncio as _aio
            elapsed = 0
            poll_interval = 3
            while elapsed < timeout_seconds:
                await _aio.sleep(poll_interval)
                elapsed += poll_interval
                cookies = await ctx.cookies("https://www.google.com")
                if _has_google_session(cookies):
                    result["ok"] = True
                    break
                remaining = timeout_seconds - elapsed
                print(f"  ⏳ 等待登入... 剩餘 {remaining}s", end="\r", flush=True)

            if result["ok"]:
                print(f"\n✅ 登入成功！（{elapsed} 秒）")
                print("  正在儲存 session...")
                # Navigate to www.google.com to trigger persistent cookie writes
                await page.goto("https://www.google.com", timeout=20_000)
                await _aio.sleep(2)
                all_cookies = await ctx.cookies()
                _save_cookies(all_cookies)

            await ctx.close()

        if result["ok"]:
            n = _count_session()
            print(
                f"  已儲存 {n} 個 session cookies 到 {_SESSION_FILE}\n"
                "\n"
                "✅ 設定完成！未來搜尋冷門路線時會自動使用你的 Google session。\n"
                "  一般路線不需要 session 也能搜。"
            )
        else:
            print(
                f"\n❌ 超時（{timeout_seconds} 秒），未偵測到登入。\n"
                "  請再次執行 gf_search.setup() 並完成 Google 登入。"
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
