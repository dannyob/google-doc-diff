#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "browser-cookie3>=0.20.1",
#   "requests>=2.31",
# ]
# ///
"""Fetch the bootstrap OT op log embedded in a Google Doc's /edit page.

The /edit response is a ~400 KB HTML blob. Buried in a `<script>` tag is

    DOCS_modelChunk = { "chunk": [ ...ops... ], "revision": N };

That JSON IS the document expressed as the same operational-transformation
operations the editor exchanges over /mutate. It's the highest-fidelity view
of the doc you can get without the editor itself.

Op kinds observed so far:

    mkch  make-chunk-header     {d: [[1, "Doc Title"]]}
    ac    add-child (tab)       {d: [tabId, [1, "Tab name"], [order]]}
    is    insert string         {ibi: int, s: str}
    iss   insert string         {sugid: str, ibi: int, s: str}     (a suggestion)
    ae    add element           {et: "list", id: "kix.list.1", epm: {...}}
    as    apply style           {st: "headings"|"paragraph"|..., si, ei, sm: {...}}
    msfd  modify section/footer/details (large bag of section props)

The `_i` suffix in property names ("ts_un_i", "ps_klt_i") means "inherited".

Usage:

    ./kix_dump_model.py DOC_ID                  # pretty summary on stdout
    ./kix_dump_model.py DOC_ID --json           # full JSON (huge)
    ./kix_dump_model.py DOC_ID --html-out X.htm # save raw /edit response too

Authenticates via local Chrome cookies (kix_cookies.py).
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from kix_cookies import load_jar  # type: ignore


DOC_RE = re.compile(r"^(?:[A-Za-z0-9_-]+)$")
URL_RE = re.compile(r"/document/d/([A-Za-z0-9_-]+)")


def normalize_doc(arg: str) -> str:
    if DOC_RE.match(arg):
        return arg
    m = URL_RE.search(arg)
    if not m:
        raise SystemExit(f"could not parse doc id from {arg!r}")
    return m.group(1)


def fetch_edit_html(doc_id: str) -> str:
    jar = load_jar()
    url = f"https://docs.google.com/document/d/{doc_id}/edit"
    headers = {
        # Pretend to be a real Chrome (matches the cookie origin).
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
    }
    r = requests.get(url, cookies=jar, headers=headers, timeout=20, allow_redirects=True)
    r.raise_for_status()
    if "DOCS_modelChunk" not in r.text:
        raise SystemExit(
            "no DOCS_modelChunk in response — likely got the unauthenticated "
            "marketing page. Check that cookies cover docs.google.com and that "
            "you have access to the doc."
        )
    return r.text


def extract_model_chunk(html: str) -> dict:
    """Pull the JSON value of `DOCS_modelChunk = {...}` out of the HTML."""
    idx = html.find("DOCS_modelChunk = ")
    if idx < 0:
        raise ValueError("DOCS_modelChunk assignment not found")
    start = html.find("{", idx)
    if start < 0:
        raise ValueError("no opening brace")
    depth = 0
    in_str = False
    esc = False
    end = -1
    for k in range(start, len(html)):
        c = html[k]
        if esc:
            esc = False
            continue
        if c == "\\":
            esc = True
            continue
        if in_str:
            if c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = k + 1
                break
    if end < 0:
        raise ValueError("unbalanced JSON")
    return json.loads(html[start:end])


def summarize(model: dict) -> str:
    ops = model.get("chunk", [])
    rev = model.get("revision", "?")
    ty_counts = collections.Counter(o.get("ty") for o in ops)
    out = []
    out.append(f"revision: {rev}")
    out.append(f"op count: {len(ops)}")
    out.append("op kinds (count):")
    for ty, n in ty_counts.most_common():
        out.append(f"  {ty:<6} {n}")
    # First op of each ty
    out.append("")
    out.append("one sample per op kind:")
    seen = set()
    for o in ops:
        ty = o.get("ty")
        if ty in seen:
            continue
        seen.add(ty)
        s = json.dumps(o, ensure_ascii=False)
        if len(s) > 220:
            s = s[:217] + "..."
        out.append(f"  {ty}: {s}")
    # Pull all `ac` (tabs) and `is` lengths
    tabs = [o["d"] for o in ops if o.get("ty") == "ac"]
    if tabs:
        out.append("")
        out.append("tabs:")
        for t in tabs:
            out.append(f"  {t}")
    is_total = sum(len(o.get("s", "")) for o in ops if o.get("ty") == "is")
    iss_total = sum(len(o.get("s", "")) for o in ops if o.get("ty") == "iss")
    out.append("")
    out.append(f"is  (insert) chars total: {is_total}")
    out.append(f"iss (insert-in-suggestion) chars total: {iss_total}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("doc")
    ap.add_argument("--json", action="store_true", help="emit full modelChunk JSON")
    ap.add_argument("--html-out", metavar="PATH")
    args = ap.parse_args()

    doc_id = normalize_doc(args.doc)
    html = fetch_edit_html(doc_id)
    if args.html_out:
        Path(args.html_out).write_text(html, encoding="utf-8")
        print(f"wrote {args.html_out} ({len(html)} bytes)", file=sys.stderr)
    model = extract_model_chunk(html)
    if args.json:
        json.dump(model, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(summarize(model))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
