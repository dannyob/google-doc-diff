#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "browser-cookie3>=0.20.1",
#   "requests>=2.31",
# ]
# ///
"""Pull the raw per-revision OT op chunks from /revisions/load.

This is the same endpoint James Somers used in 2014. URL pattern:

    GET /document/d/<docid>/revisions/load
        ?id=<docid>
        &start=<int>
        &end=<int>

The response is the standard Google ")]}'\n" XSSI prefix followed by JSON of
the shape:

    { "changelog": [ <op>, ... ],   # each <op> is the same OT op format
      "endRevision": <int>,
      "endRevisionType": "...",
      ... }

Usage:

    ./kix_revisions_load.py DOC                    # dump rev 1..max
    ./kix_revisions_load.py DOC --start 1 --end 50
    ./kix_revisions_load.py DOC --probe-max        # binary-search for max rev
    ./kix_revisions_load.py DOC --summary          # group ops by ty/author

The revision counter is "per OT chunk" — much finer-grained than the named
revisions Drive's /revisions API exposes. A single typed character is one rev.
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


XSSI_PREFIX = ")]}'\n"
URL_RE = re.compile(r"/document/d/([A-Za-z0-9_-]+)")


def normalize_doc(arg: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", arg):
        return arg
    m = URL_RE.search(arg)
    if not m:
        raise SystemExit(f"could not parse doc id from {arg!r}")
    return m.group(1)


def fetch_range(doc: str, start: int, end: int) -> dict | None:
    jar = load_jar()
    url = f"https://docs.google.com/document/d/{doc}/revisions/load"
    params = {"id": doc, "start": start, "end": end}
    headers = {"x-same-domain": "1"}
    r = requests.get(url, cookies=jar, params=params, headers=headers, timeout=20)
    if r.status_code >= 400:
        return {"_error": r.status_code, "_body": r.text[:300]}
    body = r.text
    if body.startswith(XSSI_PREFIX):
        body = body[len(XSSI_PREFIX):]
    if not body.strip():
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        return {"_decode_error": str(e), "_body_preview": body[:400]}


def probe_max(doc: str, hint_upper: int = 1_000_000) -> int:
    """Binary-search the highest revision that returns a non-empty changelog."""
    lo, hi = 1, hint_upper
    # exponential probe to find a hi that errors / returns empty
    cur = 8
    while cur < hi:
        r = fetch_range(doc, cur, cur)
        if r and not r.get("_error") and r.get("changelog"):
            cur *= 2
        else:
            hi = cur
            break
    while lo < hi:
        mid = (lo + hi + 1) // 2
        r = fetch_range(doc, mid, mid)
        ok = bool(r and not r.get("_error") and r.get("changelog"))
        if ok:
            lo = mid
        else:
            hi = mid - 1
    return lo


def summarize_changelog(doc_arg: str, start: int, end: int) -> str:
    out = []
    data = fetch_range(doc_arg, start, end)
    if not data:
        return "(empty)"
    if data.get("_error"):
        return f"HTTP {data['_error']}: {data['_body']}"
    if data.get("_decode_error"):
        return f"decode error: {data['_decode_error']}\n{data['_body_preview']}"
    out.append(f"keys: {sorted(data.keys())}")
    if "changelog" in data:
        cl = data["changelog"]
        out.append(f"changelog len: {len(cl)}")
        from collections import Counter

        # changelog items are sometimes [ts, sid, [ops...], revNum] tuples; probe shape
        if cl:
            first = cl[0]
            out.append(f"first item type: {type(first).__name__}")
            out.append(f"first item preview: {json.dumps(first)[:300]}")
            if isinstance(first, list) and len(first) >= 3 and isinstance(first[2], list):
                ty_counts = Counter()
                for entry in cl:
                    for op in entry[2]:
                        if isinstance(op, dict) and "ty" in op:
                            ty_counts[op["ty"]] += 1
                out.append("op kinds in changelog:")
                for ty, n in ty_counts.most_common():
                    out.append(f"  {ty:<6} {n}")
    for k in ("endRevision", "endRevisionType", "firstRev", "lastModifiedTime"):
        if k in data:
            out.append(f"{k}: {data[k]}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("doc")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=10000)
    ap.add_argument("--probe-max", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--json", action="store_true", help="dump raw JSON")
    args = ap.parse_args()
    doc = normalize_doc(args.doc)

    if args.probe_max:
        m = probe_max(doc)
        print(f"max revision: {m}")
        return 0

    if args.json:
        data = fetch_range(doc, args.start, args.end)
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0

    print(summarize_changelog(doc, args.start, args.end))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
