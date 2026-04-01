"""
Unit tests for gf_search.setup helpers (offline, no browser launched).
"""

import json
import pytest
from pathlib import Path
from gf_search.setup import load_session_cookies, _has_google_session, _SESSION_COOKIE_NAMES


class TestLoadSessionCookies:

    def _setup_mod(self):
        # gf_search/__init__.py does `from .setup import setup`, which shadows
        # the submodule name in the gf_search namespace.  Use sys.modules to
        # get the actual module object regardless.
        import sys
        import gf_search.setup  # ensure submodule is loaded
        return sys.modules["gf_search.setup"]

    def test_returns_empty_when_file_missing(self, tmp_path, monkeypatch):
        """load_session_cookies() must return [] if session_cookies.json doesn't exist."""
        setup_mod = self._setup_mod()
        monkeypatch.setattr(setup_mod, "_SESSION_FILE", str(tmp_path / "nonexistent.json"))
        assert load_session_cookies() == []

    def test_returns_list_when_file_valid(self, tmp_path, monkeypatch):
        setup_mod = self._setup_mod()
        cookies = [
            {"name": "SID", "value": "abc123", "domain": ".google.com",
             "path": "/", "secure": True, "httpOnly": False,
             "sameSite": "None", "expires": 1809586474},
        ]
        path = tmp_path / "session_cookies.json"
        path.write_text(json.dumps(cookies), encoding="utf-8")
        monkeypatch.setattr(setup_mod, "_SESSION_FILE", str(path))
        result = load_session_cookies()
        assert result == cookies

    def test_returns_empty_on_invalid_json(self, tmp_path, monkeypatch):
        setup_mod = self._setup_mod()
        path = tmp_path / "session_cookies.json"
        path.write_text("NOT VALID JSON", encoding="utf-8")
        monkeypatch.setattr(setup_mod, "_SESSION_FILE", str(path))
        assert load_session_cookies() == []

    def test_returns_empty_list_for_empty_array(self, tmp_path, monkeypatch):
        setup_mod = self._setup_mod()
        path = tmp_path / "session_cookies.json"
        path.write_text("[]", encoding="utf-8")
        monkeypatch.setattr(setup_mod, "_SESSION_FILE", str(path))
        assert load_session_cookies() == []


class TestHasGoogleSession:

    def test_empty_list(self):
        assert _has_google_session([]) is False

    def test_no_session_cookies(self):
        cookies = [{"name": "NID"}, {"name": "CONSENT"}]
        assert _has_google_session(cookies) is False

    @pytest.mark.parametrize("name", sorted(_SESSION_COOKIE_NAMES))
    def test_detects_session_cookie(self, name):
        cookies = [{"name": name, "value": "xxx"}]
        assert _has_google_session(cookies) is True

    def test_mixed_cookies(self):
        cookies = [
            {"name": "NID", "value": "x"},
            {"name": "SID", "value": "y"},
            {"name": "OTZ", "value": "z"},
        ]
        assert _has_google_session(cookies) is True
