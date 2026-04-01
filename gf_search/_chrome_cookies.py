"""
Read Google session cookies from Chrome on Windows (optional enhancement).

If the user has Chrome with a Google account, injecting real session cookies
causes Google's SSR to return session-aware results — including carriers like
Cathay Pacific (CX) that are only returned for authenticated sessions.

Two strategies, tried in order:
  1. Chrome DevTools Protocol (CDP) – requires Chrome running with
     --remote-debugging-port=9222.  Works while Chrome is open; returns
     already-decrypted cookies so no DPAPI/AES work is needed.
  2. SQLite file copy – works when Chrome is closed.  Requires pywin32 +
     pycryptodome (both optional).

``get_chrome_cookies_playwright()`` returns the same cookies in the dict
format that Playwright's ``BrowserContext.add_cookies()`` expects, using the
Windows CreateFileW/ReadFile API to bypass SQLite file locks (works while
Chrome is running).

All imports are optional; returns "" / [] gracefully if prerequisites are missing.
"""

from __future__ import annotations

_GOOGLE_COOKIE_NAMES = (
    "SID", "SSID", "APISID", "SAPISID",
    "__Secure-1PSID", "__Secure-3PSID",
    "__Secure-1PSIDCC", "__Secure-3PSIDCC",
    "NID", "CONSENT", "1P_JAR",
)


def _get_google_session_cookie_cdp() -> str:
    """
    Fetch google.com cookies from a running Chrome via CDP.
    Requires: Chrome started with --remote-debugging-port=9222
              websocket-client  (pip install websocket-client)
    Returns "" if CDP is unavailable or no google.com cookies found.
    """
    import json
    import urllib.request

    try:
        import websocket  # websocket-client
    except ImportError:
        return ""

    # ── 1. Check if Chrome has remote debugging enabled ───────────────────────
    try:
        req = urllib.request.urlopen("http://localhost:9222/json/list", timeout=2)
        tabs = json.loads(req.read())
    except Exception:
        return ""

    if not tabs:
        return ""

    # ── 2. Connect to first available page target ─────────────────────────────
    ws_url = tabs[0].get("webSocketDebuggerUrl", "")
    if not ws_url:
        return ""

    data: dict = {}
    try:
        ws = websocket.create_connection(ws_url, timeout=5)
        ws.send(json.dumps({
            "id": 1,
            "method": "Network.getAllCookies",
            "params": {},
        }))
        for _ in range(30):
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                data = msg
                break
        ws.close()
    except Exception:
        return ""

    cookies = data.get("result", {}).get("cookies", [])
    allowed = set(_GOOGLE_COOKIE_NAMES)

    parts = [
        f"{c['name']}={c['value']}"
        for c in cookies
        if (
            c.get("domain", "").endswith("google.com")
            and c["name"] in allowed
            and c.get("value", "")
        )
    ]
    return "; ".join(parts)


def get_google_session_cookie() -> str:
    """
    Return a Cookie header string with Google session cookies from Chrome,
    or "" if unavailable (Chrome not installed, not Windows, missing deps, etc.).

    Tries CDP first (works while Chrome is running with --remote-debugging-port=9222),
    then falls back to the SQLite approach (works when Chrome is closed).
    """
    # ── Strategy 1: CDP (Chrome running with --remote-debugging-port=9222) ────
    try:
        cdp_cookies = _get_google_session_cookie_cdp()
        if cdp_cookies:
            return cdp_cookies
    except Exception:
        pass

    # ── Strategy 2: SQLite file copy (Chrome must be closed) ─────────────────
    import json, base64, sqlite3, shutil, os, tempfile

    try:
        local_state_path = os.path.expandvars(
            r'%LOCALAPPDATA%\Google\Chrome\User Data\Local State'
        )
        if not os.path.exists(local_state_path):
            return ""

        with open(local_state_path, encoding='utf-8') as f:
            ls = json.load(f)

        # AES key is DPAPI-encrypted, b64-decoded, strip 5-byte "DPAPI" prefix
        enc_key = base64.b64decode(ls['os_crypt']['encrypted_key'])[5:]
        from win32crypt import CryptUnprotectData
        _, aes_key = CryptUnprotectData(enc_key, None, None, None, 0)

        db_candidates = [
            os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\User Data\Default\Network\Cookies'),
            os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\User Data\Default\Cookies'),
        ]
        db_path = next((p for p in db_candidates if os.path.exists(p)), None)
        if not db_path:
            return ""

        tmp = _read_sqlite_file_windows(db_path)
        if not tmp:
            return ""

        conn = sqlite3.connect(tmp)
        rows = conn.execute(
            "SELECT name, encrypted_value FROM cookies "
            "WHERE host_key LIKE '%.google.com' "
            "AND name IN ('SID','SSID','APISID','SAPISID',"
            "'__Secure-1PSID','__Secure-3PSID',"
            "'__Secure-1PSIDCC','__Secure-3PSIDCC','NID','CONSENT','1P_JAR')"
        ).fetchall()
        conn.close()
        try:
            os.unlink(tmp)
        except Exception:
            pass

        try:
            from Cryptodome.Cipher import AES
        except ImportError:
            try:
                from Crypto.Cipher import AES
            except ImportError:
                return ""

        parts: list[str] = []
        for name, ev in rows:
            ev = bytes(ev)
            try:
                if ev[:3] in (b'v10', b'v11'):
                    # Chrome 80+: AES-256-GCM
                    nonce = ev[3:15]
                    ct    = ev[15:-16]
                    tag   = ev[-16:]
                    val   = AES.new(aes_key, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(ct, tag).decode('utf-8')
                else:
                    # Older DPAPI direct encryption
                    from win32crypt import CryptUnprotectData as _cupd
                    _, vb = _cupd(ev, None, None, None, 0)
                    val = vb.decode('utf-8')
                parts.append(f"{name}={val}")
            except Exception:
                continue

        return "; ".join(parts)
    except Exception:
        return ""


def _read_sqlite_file_windows(db_path: str) -> str:
    """
    Copy a SQLite file to a temp path using Windows CreateFileW/ReadFile,
    bypassing the file lock held by a running Chrome process.

    Returns the temp file path on success, or "" on failure.
    Only available on Windows; returns "" on other platforms.
    """
    import ctypes, ctypes.wintypes, os, tempfile, sys

    if sys.platform != "win32":
        import shutil
        fd, tmp = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        try:
            shutil.copy2(db_path, tmp)
            return tmp
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            return ""

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.restype = ctypes.wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        ctypes.wintypes.LPCWSTR, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD,
        ctypes.c_void_p, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD,
        ctypes.wintypes.HANDLE,
    ]
    kernel32.ReadFile.argtypes = [
        ctypes.wintypes.HANDLE, ctypes.c_void_p, ctypes.wintypes.DWORD,
        ctypes.POINTER(ctypes.wintypes.DWORD), ctypes.c_void_p,
    ]
    kernel32.ReadFile.restype = ctypes.wintypes.BOOL
    kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]

    h = kernel32.CreateFileW(
        db_path,
        0x80000000,        # GENERIC_READ
        1 | 2 | 4,         # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
        None,
        3,                 # OPEN_EXISTING
        0x02000000,        # FILE_FLAG_BACKUP_SEMANTICS
        None,
    )
    INVALID = ctypes.wintypes.HANDLE(-1).value
    if h == INVALID:
        return ""

    fd, tmp = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    buf = (ctypes.c_char * 65536)()
    br = ctypes.wintypes.DWORD(0)
    total = 0
    try:
        with open(tmp, 'wb') as outf:
            while True:
                ok = kernel32.ReadFile(h, buf, 65536, ctypes.byref(br), None)
                if not ok or br.value == 0:
                    break
                outf.write(buf.raw[:br.value])
                total += br.value
    finally:
        kernel32.CloseHandle(h)

    if total == 0:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        return ""
    return tmp


def get_chrome_cookies_playwright() -> list[dict]:
    """
    Return Chrome's Google session cookies as a list of Playwright cookie dicts
    (suitable for ``BrowserContext.add_cookies()``).

    Uses the Windows API to read the locked SQLite file while Chrome is running.
    Returns [] if any prerequisite is missing or decryption fails.
    """
    import json, base64, sqlite3, os

    try:
        from Cryptodome.Cipher import AES
    except ImportError:
        try:
            from Crypto.Cipher import AES  # type: ignore
        except ImportError:
            return []

    try:
        from win32crypt import CryptUnprotectData
    except ImportError:
        return []

    ls_path = os.path.expandvars(
        r'%LOCALAPPDATA%\Google\Chrome\User Data\Local State'
    )
    db_candidates = [
        os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\User Data\Default\Network\Cookies'),
        os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\User Data\Default\Cookies'),
    ]
    db_path = next((p for p in db_candidates if os.path.exists(p)), None)
    if not (os.path.exists(ls_path) and db_path):
        return []

    try:
        with open(ls_path, encoding='utf-8') as f:
            ls = json.load(f)
        enc_key = base64.b64decode(ls['os_crypt']['encrypted_key'])[5:]
        _, aes_key = CryptUnprotectData(enc_key, None, None, None, 0)
    except Exception:
        return []

    tmp = _read_sqlite_file_windows(db_path)
    if not tmp:
        return []

    rows = []
    try:
        conn = sqlite3.connect(tmp)
        rows = conn.execute(
            "SELECT host_key, name, path, encrypted_value, expires_utc, is_secure "
            "FROM cookies WHERE host_key LIKE '%.google.com'"
        ).fetchall()
        conn.close()
    except Exception:
        pass
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass

    cookies: list[dict] = []
    for host_key, name, path, ev, expires_utc, is_secure in rows:
        ev = bytes(ev)
        if ev[:3] not in (b'v10', b'v11'):
            continue  # skip v20+ App-Bound Encrypted cookies
        try:
            nonce, ct, tag = ev[3:15], ev[15:-16], ev[-16:]
            val = AES.new(aes_key, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(ct, tag).decode('utf-8')
        except Exception:
            continue
        # Chrome stores expiry as microseconds since 1601-01-01; convert to Unix epoch
        exp_unix = (expires_utc // 1_000_000) - 11_644_473_600 if expires_utc > 0 else -1
        domain = host_key if host_key.startswith('.') else '.' + host_key.lstrip('.')
        cookies.append({
            'name': name,
            'value': val,
            'domain': domain,
            'path': path,
            'secure': bool(is_secure),
            'httpOnly': False,
            'sameSite': 'None',
            'expires': exp_unix,
        })
    return cookies
