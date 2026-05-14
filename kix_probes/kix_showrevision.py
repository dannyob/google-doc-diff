#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "browser-cookie3>=0.20.1",
#   "requests>=2.31",
# ]
# ///
"""Fetch the OT chunked snapshot for a revision range via /showrevision.

The current Docs editor talks to ``/document/u/<authuser>/d/<id>/showrevision``
instead of the older ``/revisions/load``. Query params:

    start, end       int revision numbers (1-based, inclusive)
    id               doc id (yes, again)
    smv              server model version watermark (use 2147483647)
    smb              server model build (use "[2147483647, oAMQ]")
    srfn, ern        "false" — show-revision-first-name, etc.
    token            per-session XSRF token, embedded in /edit HTML as
                     `"info_params":{"token":"AOqKD6...:1778..."}`
    ouid             user's obfuscated id (also from info_params)
    includes_info_params=true, cros_files=false, nded=false, tab=t.0

Response is ``)]}'\\n`` XSSI-prefixed JSON:

    {
      "chunkedSnapshot": [ [op, ...], [op, ...], ... ],  # OT ops by chunk
      "userInfo":  {...},
      "suggestionColors": { "suggest.<id>": "#RRGGBB", ... },
      "nestedDrawingRevisionDiffResults": {...}
    }

The ops are the same shape as DOCS_modelChunk: mkch, ac, is, iss, ae, as, msfd,
plus the per-keystroke ops (insert, delete, multi, etc.) that arrive at runtime.

Usage:

    ./kix_showrevision.py DOC --start 1 --end 1
    ./kix_showrevision.py DOC --json > snapshot.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from kix_cookies import load_jar  # type: ignore


XSSI = ")]}'\n"
DOC_RE = re.compile(r"/document/d/([A-Za-z0-9_-]+)")


def normalize_doc(arg: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", arg):
        return arg
    m = DOC_RE.search(arg)
    if not m:
        raise SystemExit(f"could not parse doc id from {arg!r}")
    return m.group(1)


def info_params(doc_id: str) -> dict:
    """Pull `info_params` from the doc's /edit HTML (gives us token + ouid)."""
    jar = load_jar()
    r = requests.get(
        f"https://docs.google.com/document/d/{doc_id}/edit",
        cookies=jar,
        headers={"user-agent": "Mozilla/5.0 Chrome/124"},
        timeout=20,
    )
    r.raise_for_status()
    m = re.search(r'"info_params"\s*:\s*(\{[^}]+\})', r.text)
    if not m:
        raise RuntimeError("no info_params in /edit HTML")
    return json.loads(m.group(1))


def showrevision(doc_id: str, start: int, end: int, tab: str = "t.0") -> dict:
    ip = info_params(doc_id)
    jar = load_jar()
    params = {
        "start": str(start),
        "end": str(end),
        "id": doc_id,
        "smv": "2147483647",
        "srfn": "false",
        "ern": "false",
        "smb": "[2147483647, oAMQ]",
        "token": ip["token"],
        "ouid": ip["ouid"],
        "includes_info_params": "true",
        "cros_files": "false",
        "nded": "false",
        "tab": tab,
    }
    r = requests.get(
        f"https://docs.google.com/document/u/1/d/{doc_id}/showrevision",
        params=params,
        cookies=jar,
        headers={"user-agent": "Mozilla/5.0 Chrome/124", "x-same-domain": "1"},
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    body = r.text
    if body.startswith(XSSI):
        body = body[len(XSSI):]
    return json.loads(body)


def summarize(snapshot: dict) -> str:
    from collections import Counter

    lines = []
    cs = snapshot.get("chunkedSnapshot", [])
    lines.append(f"chunkedSnapshot: {len(cs)} chunk(s)")
    total_ops = 0
    ty_counter = Counter()
    for chunk in cs:
        for op in chunk:
            total_ops += 1
            if isinstance(op, dict):
                ty_counter[op.get("ty")] += 1
    lines.append(f"total ops: {total_ops}")
    if ty_counter:
        lines.append("op kinds:")
        for ty, n in ty_counter.most_common():
            lines.append(f"  {ty:<6} {n}")
    if snapshot.get("suggestionColors"):
        lines.append(f"suggestions: {list(snapshot['suggestionColors'].keys())}")
    if snapshot.get("userInfo"):
        lines.append(f"userInfo entries: {len(snapshot['userInfo'])}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("doc")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=1)
    ap.add_argument("--tab", default="t.0")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    doc = normalize_doc(args.doc)
    snap = showrevision(doc, args.start, args.end, tab=args.tab)
    if args.json:
        json.dump(snap, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(summarize(snap))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
