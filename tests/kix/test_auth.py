"""Tests for kix.auth — cookie resolution and session loading."""

import os
from pathlib import Path
from unittest.mock import patch

from google_doc_diff.kix.auth import resolve_cookie_path


def test_explicit_path_env(tmp_path):
    cookies = tmp_path / "Cookies"
    cookies.write_bytes(b"")
    with patch.dict(os.environ, {"GDOC_KIX_COOKIES": str(cookies)}):
        assert resolve_cookie_path() == cookies


def test_explicit_path_kwarg(tmp_path):
    cookies = tmp_path / "Cookies"
    cookies.write_bytes(b"")
    assert resolve_cookie_path(cookie_path=str(cookies)) == cookies


def test_kwarg_overrides_env(tmp_path):
    env_cookies = tmp_path / "env" / "Cookies"
    env_cookies.parent.mkdir()
    env_cookies.write_bytes(b"")
    kwarg_cookies = tmp_path / "kwarg" / "Cookies"
    kwarg_cookies.parent.mkdir()
    kwarg_cookies.write_bytes(b"")
    with patch.dict(os.environ, {"GDOC_KIX_COOKIES": str(env_cookies)}):
        assert resolve_cookie_path(cookie_path=str(kwarg_cookies)) == kwarg_cookies


def test_profile_name_resolves(tmp_path):
    profile_dir = tmp_path / "Profile 1"
    profile_dir.mkdir()
    cookies = profile_dir / "Cookies"
    cookies.write_bytes(b"")
    with patch("google_doc_diff.kix.auth.CHROME_ROOT", tmp_path), \
         patch.dict(os.environ, {"GDOC_KIX_COOKIES": "", "GDOC_KIX_PROFILE": ""}, clear=False):
        assert resolve_cookie_path(profile_name="Profile 1") == cookies


def test_profile_env(tmp_path):
    profile_dir = tmp_path / "MyProfile"
    profile_dir.mkdir()
    cookies = profile_dir / "Cookies"
    cookies.write_bytes(b"")
    with patch("google_doc_diff.kix.auth.CHROME_ROOT", tmp_path), \
         patch.dict(os.environ, {"GDOC_KIX_COOKIES": "", "GDOC_KIX_PROFILE": "MyProfile"}):
        assert resolve_cookie_path() == cookies


def test_auto_detect_picks_newest(tmp_path):
    old = tmp_path / "Default" / "Cookies"
    old.parent.mkdir()
    old.write_bytes(b"")
    new = tmp_path / "Profile 1" / "Cookies"
    new.parent.mkdir()
    new.write_bytes(b"")
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))
    with patch("google_doc_diff.kix.auth.CHROME_ROOT", tmp_path), \
         patch.dict(os.environ, {"GDOC_KIX_COOKIES": "", "GDOC_KIX_PROFILE": ""}, clear=False):
        assert resolve_cookie_path() == new


def test_no_chrome_returns_none(tmp_path):
    with patch("google_doc_diff.kix.auth.CHROME_ROOT", tmp_path), \
         patch.dict(os.environ, {"GDOC_KIX_COOKIES": "", "GDOC_KIX_PROFILE": ""}, clear=False):
        assert resolve_cookie_path() is None


def test_missing_explicit_path_returns_none():
    assert resolve_cookie_path(cookie_path="/nonexistent/Cookies") is None


from google_doc_diff.kix.auth import scrape_info_params, scrape_role


INFO_PARAMS_HTML = '''
<script>var defined = {"info_params":{"token":"AOqKD6abc:1778712727467","ouid":"123456789"}}</script>
'''


def test_scrape_info_params():
    result = scrape_info_params(INFO_PARAMS_HTML)
    assert result is not None
    assert result["token"] == "AOqKD6abc:1778712727467"
    assert result["ouid"] == "123456789"


def test_scrape_info_params_missing():
    assert scrape_info_params("<html>nothing here</html>") is None


ROLE_HTML_EDIT_SCOPE = '"editingMode":"EDITING"'
ROLE_HTML_VIEW_SCOPE = '"editingMode":"VIEWING"'
ROLE_HTML_SUGGEST_SCOPE = '"editingMode":"SUGGESTING"'
ROLE_HTML_NONE = '<html>no role info</html>'


def test_scrape_role_editor():
    assert scrape_role(ROLE_HTML_EDIT_SCOPE) == "editor"


def test_scrape_role_viewer():
    assert scrape_role(ROLE_HTML_VIEW_SCOPE) == "viewer"


def test_scrape_role_commenter():
    assert scrape_role(ROLE_HTML_SUGGEST_SCOPE) == "commenter"


def test_scrape_role_unknown():
    assert scrape_role(ROLE_HTML_NONE) == "unknown"
