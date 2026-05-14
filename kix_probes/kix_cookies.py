#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "browser-cookie3>=0.20.1",
# ]
# ///
"""Extract docs.google.com cookies from the local Chrome profile.

macOS: Chrome stores cookies in
``~/Library/Application Support/Google/Chrome/<profile>/Cookies`` (SQLite).
Encrypted values are AES-CBC-encrypted with a key derived from the
"Chrome Safe Storage" entry in the macOS keychain. `browser-cookie3` handles
all of that.

Usage:

    ./kix_cookies.py                    # print cookie names found
    ./kix_cookies.py --dump-jar PATH    # write as Netscape cookie jar
    ./kix_cookies.py --as-header        # one Cookie: header line

The Kix editor needs at least: SID, HSID, SSID, APISID, SAPISID,
__Secure-1PSID, __Secure-1PAPISID, __Secure-3PAPISID (and refreshes them).
"""
from __future__ import annotations

import argparse
import os
import sys
from http.cookiejar import MozillaCookieJar
from pathlib import Path

import browser_cookie3

CHROME_ROOT = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
# Env override; otherwise auto-pick the most recently used google-signed-in profile.
DEFAULT_PROFILE_ENV = "KIX_CHROME_PROFILE"


def _pick_profile() -> Path:
    override = os.environ.get(DEFAULT_PROFILE_ENV)
    if override:
        p = CHROME_ROOT / override / "Cookies"
        if not p.exists():
            raise SystemExit(f"{DEFAULT_PROFILE_ENV}={override}: no Cookies at {p}")
        return p
    # macOS Chrome: profiles are "Default", "Profile 1", "Profile 2", ... .
    # Pick the most-recently-modified Cookies file as a reasonable default.
    candidates = sorted(
        (d / "Cookies" for d in CHROME_ROOT.iterdir() if (d / "Cookies").exists()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise SystemExit(f"no Chrome Cookies files under {CHROME_ROOT}")
    return candidates[0]


def load_jar(profile: str | None = None) -> MozillaCookieJar:
    """Return a MozillaCookieJar populated with google.com / docs.google.com
    cookies from the local Chrome profile.

    Picks the most-recently-touched Chrome profile (typically the one the user
    is actively signed in to). Override with ``KIX_CHROME_PROFILE=Profile 1``.
    """
    cookie_file = _pick_profile()
    jar = browser_cookie3.chrome(cookie_file=str(cookie_file), domain_name=".google.com")
    out = MozillaCookieJar()
    for c in jar:
        out.set_cookie(c)
    return out


def cookie_header(jar: MozillaCookieJar, domain: str = "docs.google.com") -> str:
    pairs = []
    for c in jar:
        if c.domain.lstrip(".") in domain or domain.endswith(c.domain.lstrip(".")):
            pairs.append(f"{c.name}={c.value}")
    return "; ".join(pairs)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dump-jar", metavar="PATH")
    ap.add_argument("--as-header", action="store_true")
    ap.add_argument("--names-only", action="store_true")
    args = ap.parse_args()

    jar = load_jar()
    if args.dump_jar:
        jar.save(args.dump_jar, ignore_discard=True, ignore_expires=True)
        print(f"wrote {args.dump_jar}", file=sys.stderr)
        return 0
    if args.as_header:
        print(cookie_header(jar))
        return 0
    names = sorted({c.name for c in jar})
    if args.names_only:
        for n in names:
            print(n)
    else:
        for n in names:
            n_cookies = [c for c in jar if c.name == n]
            domains = {c.domain for c in n_cookies}
            print(f"  {n:<32} ({', '.join(sorted(domains))})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
