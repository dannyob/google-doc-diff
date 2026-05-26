"""Kix auth: Chrome cookie loading and /edit session establishment."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from http.cookiejar import MozillaCookieJar
from pathlib import Path

logger = logging.getLogger(__name__)

if sys.platform == "darwin":
    CHROME_ROOT = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
else:
    CHROME_ROOT = Path.home() / ".config" / "google-chrome"


@dataclass
class KixSession:
    """A loaded kix session with cookies, auth tokens, and cached /edit HTML."""

    jar: MozillaCookieJar
    token: str
    ouid: str
    doc_id: str
    role: str
    edit_html: str


def kix_available() -> bool:
    """True if browser-cookie3 is importable and Chrome cookies exist."""
    try:
        import browser_cookie3  # noqa: F401
    except ImportError:
        return False
    return resolve_cookie_path() is not None


def resolve_cookie_path(
    *,
    cookie_path: str | None = None,
    profile_name: str | None = None,
) -> Path | None:
    """Resolve a Chrome Cookies SQLite path.

    Priority: explicit cookie_path kwarg > GDOC_KIX_COOKIES env >
    profile_name kwarg > GDOC_KIX_PROFILE env > auto-detect newest.
    Returns None if no valid path found.
    """
    path = cookie_path or os.environ.get("GDOC_KIX_COOKIES") or None
    if path:
        p = Path(path)
        return p if p.is_file() else None

    profile = profile_name or os.environ.get("GDOC_KIX_PROFILE") or None
    if profile:
        p = CHROME_ROOT / profile / "Cookies"
        return p if p.is_file() else None

    if not CHROME_ROOT.is_dir():
        return None
    candidates = sorted(
        (d / "Cookies" for d in CHROME_ROOT.iterdir() if (d / "Cookies").is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def load_kix_session(
    doc_id: str,
    *,
    cookie_path: str | None = None,
    profile_name: str | None = None,
) -> KixSession | None:
    """Load cookies, fetch /edit, return a KixSession or None on failure."""
    try:
        import browser_cookie3
    except ImportError:
        logger.debug("browser-cookie3 not installed; skipping kix")
        return None

    resolved = resolve_cookie_path(cookie_path=cookie_path, profile_name=profile_name)
    if resolved is None:
        logger.debug("no Chrome Cookies file found; skipping kix")
        return None

    try:
        raw_jar = browser_cookie3.chrome(
            cookie_file=str(resolved), domain_name=".google.com",
        )
    except Exception as exc:
        logger.debug("failed to load Chrome cookies: %s", exc)
        return None

    jar = MozillaCookieJar()
    for c in raw_jar:
        jar.set_cookie(c)

    try:
        import requests
        url = f"https://docs.google.com/document/d/{doc_id}/edit"
        r = requests.get(
            url, cookies=jar, timeout=20, allow_redirects=True,
            headers={
                "user-agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        r.raise_for_status()
    except Exception as exc:
        logger.debug("kix /edit fetch failed: %s", exc)
        return None

    html = r.text
    if "DOCS_modelChunk" not in html:
        logger.debug("kix /edit response has no DOCS_modelChunk; likely unauthenticated")
        return None

    ip = scrape_info_params(html)
    if ip is None:
        logger.debug("kix /edit: could not scrape info_params")
        return None

    return KixSession(
        jar=jar,
        token=ip["token"],
        ouid=ip["ouid"],
        doc_id=doc_id,
        role=scrape_role(html),
        edit_html=html,
    )


def scrape_info_params(html: str) -> dict | None:
    """Extract the token and ouid from the /edit page's info_params JSON."""
    m = re.search(r'"info_params"\s*:\s*(\{[^}]+\})', html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def scrape_role(html: str) -> str:
    """Best-effort role detection from the /edit HTML."""
    m = re.search(r'"editingMode"\s*:\s*"(\w+)"', html)
    if m:
        mode = m.group(1)
        if mode == "EDITING":
            return "editor"
        if mode == "VIEWING":
            return "viewer"
        if mode == "SUGGESTING":
            return "commenter"
    return "unknown"
