"""Tests for api.parse_doc_id (the rest of api.py needs live API or heavy mocking)."""

import pytest

from google_doc_diff.api import APIError, drive_url_for, parse_doc_id


@pytest.mark.parametrize("inp,expected", [
    ("1aBcDeFGhIjKLMNoPqRsTuVwXyZ_example_id_1234",
     "1aBcDeFGhIjKLMNoPqRsTuVwXyZ_example_id_1234"),
    ("https://docs.google.com/document/d/1aBcDeFGhIjKLMN/edit",
     "1aBcDeFGhIjKLMN"),
    ("https://docs.google.com/document/d/1aBcDeFGhIjKLMN/edit?tab=t.0",
     "1aBcDeFGhIjKLMN"),
    ("https://docs.google.com/document/d/1aBcDeFGhIjKLMN/edit?tab=t.etklgkun8k5h#heading=h.x",
     "1aBcDeFGhIjKLMN"),
    ("https://drive.google.com/file/d/SHORT_ID/view",
     "SHORT_ID"),
])
def test_parse_doc_id(inp, expected):
    assert parse_doc_id(inp) == expected


def test_parse_doc_id_raises_on_garbage():
    with pytest.raises(APIError):
        parse_doc_id("not a url and too short")


def test_drive_url_for_canonical():
    assert drive_url_for("ABC123") == "https://docs.google.com/document/d/ABC123/edit"


# -- _with_backoff retry behavior --------------------------------------------

import json

import httplib2
from googleapiclient.errors import HttpError

from google_doc_diff.api import GdocAPI


def _http_error(status: int, reason: str) -> HttpError:
    resp = httplib2.Response({"status": str(status)})
    content = json.dumps({
        "error": {
            "code": status,
            "message": "Rate Limit Exceeded" if "ate" in reason else "Forbidden",
            "errors": [{"domain": "usageLimits", "reason": reason,
                        "message": "Rate Limit Exceeded"}],
        }
    }).encode()
    return HttpError(resp, content, uri="https://example.invalid/")


def _bare_api(monkeypatch) -> GdocAPI:
    """GdocAPI without __init__ (no service builds); silence retry sleeps."""
    api = object.__new__(GdocAPI)
    monkeypatch.setattr(GdocAPI, "_sleep_for_attempt", staticmethod(lambda attempt: None))
    return api


def test_with_backoff_retries_403_rate_limit(monkeypatch):
    api = _bare_api(monkeypatch)
    calls = {"n": 0}

    class Request:
        def execute(self):
            calls["n"] += 1
            if calls["n"] < 3:
                raise _http_error(403, "rateLimitExceeded")
            return {"ok": True}

    assert api._with_backoff(lambda: Request()) == {"ok": True}
    assert calls["n"] == 3


def test_with_backoff_retries_403_user_rate_limit(monkeypatch):
    api = _bare_api(monkeypatch)
    calls = {"n": 0}

    class Request:
        def execute(self):
            calls["n"] += 1
            if calls["n"] < 2:
                raise _http_error(403, "userRateLimitExceeded")
            return {"ok": True}

    assert api._with_backoff(lambda: Request()) == {"ok": True}
    assert calls["n"] == 2


def test_with_backoff_does_not_retry_plain_403(monkeypatch):
    api = _bare_api(monkeypatch)
    calls = {"n": 0}

    class Request:
        def execute(self):
            calls["n"] += 1
            raise _http_error(403, "insufficientPermissions")

    with pytest.raises(HttpError):
        api._with_backoff(lambda: Request())
    assert calls["n"] == 1


# -- _do_get / _with_backoff_http transient handling --------------------------

import requests

from google_doc_diff.api import _Transient


def test_do_get_raises_transient_on_timeout(monkeypatch):
    api = _bare_api(monkeypatch)
    api._creds = type("C", (), {"token": "tok"})()

    def fake_get(url, headers=None, timeout=None):
        raise requests.Timeout("read timed out")

    monkeypatch.setattr("google_doc_diff.api.requests.get", fake_get)
    with pytest.raises(_Transient):
        api._do_get("https://example.invalid/export")


def test_do_get_uses_generous_timeout(monkeypatch):
    """Markdown exports of large docs exceed 30s; give them room."""
    api = _bare_api(monkeypatch)
    api._creds = type("C", (), {"token": "tok"})()
    seen = {}

    class Resp:
        status_code = 200
        content = b"ok"

    def fake_get(url, headers=None, timeout=None):
        seen["timeout"] = timeout
        return Resp()

    monkeypatch.setattr("google_doc_diff.api.requests.get", fake_get)
    assert api._do_get("https://example.invalid/export") == b"ok"
    assert seen["timeout"] >= 120


def test_with_backoff_http_retries_transient(monkeypatch):
    api = _bare_api(monkeypatch)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _Transient(599)
        return b"data"

    assert api._with_backoff_http(fn) == b"data"
    assert calls["n"] == 3
