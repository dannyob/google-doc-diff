#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "browser-cookie3>=0.20.1",
#   "requests>=2.31",
# ]
# ///
"""POST a single OT bundle to /save and print the ack.

End-to-end demonstration that we can author writes through the editor's
internal mutate channel. The default mode sends an `is` + `ds` pair, which
the server transforms into ``{"ty":"null"}`` (a no-op) before broadcast — so
the doc state is unchanged but every other client sees a revision tick.

URL:    POST /document/u/1/d/<id>/save?id=...&sid=...&token=...&ouid=...&...
Body:   application/x-www-form-urlencoded
        rev=<baseline-revision-int>
        bundles=[
          {
            "commands": [<OT op>, ...],
            "sid": "<16-hex session id>",
            "reqId": <int, monotonic>
          }
        ]

Response (after the XSSI prefix):

    {
      "revisionRanges": [[N, N]],            # what rev(s) our save occupies
      "metadata": {
        "needsTransformOnClient": false,
        "serverRevision": M                  # server's last rev before ours
      },
      "ackMessages": [{
        "c": [[<op-after-transform>, ts_ms, user_ouid, rev, sid, reqId, ?, ?, isUndo]],
        "mv": <model_version>,
        "fv": <finalized_version>,
        "mfb": [<mv>, "<frontend-build-marker>"],
        "t": "<ack-token>"
      }]
    }

Auth: the server validates cookies + `token` + `ouid` (all reachable from a
single GET /edit). The `sid` field is just an opaque identifier echoed back
in the ack — a freshly-generated 16-hex string is fine. No /bind handshake
required.

WARNING: even a no-op /save bumps the doc's revision counter — Drive's
revisions API may surface a new auto-saved version a few seconds later.
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from kix_cookies import load_jar  # type: ignore


XSSI = ")]}'\n"


def normalize_doc(arg: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", arg):
        return arg
    m = re.search(r"/document/d/([A-Za-z0-9_-]+)", arg)
    if not m:
        raise SystemExit(f"could not parse doc id from {arg!r}")
    return m.group(1)


def info_params(jar, doc_id: str) -> dict:
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


def make_sid() -> str:
    """The /save endpoint doesn't care if the sid came from /bind. Any 16-hex
    string will do — it's echoed back in the ack so co-editors can ignore the
    bundle as their own. Tested on docs.google.com 2026-05.
    """
    return secrets.token_hex(8)


def post_save(jar, doc_id: str, *, sid: str, token: str, ouid: str,
              rev: int, commands: list[dict], req_id: int,
              tab: str = "t.0") -> dict:
    params = {
        "id": doc_id, "sid": sid, "vc": "1", "c": "1", "w": "1", "flr": "0",
        "smv": "2147483647", "smb": "[2147483647, oAMQ]",
        "token": token, "ouid": ouid,
        "includes_info_params": "true", "cros_files": "false", "nded": "false",
        "tab": tab,
    }
    bundles = json.dumps([{"commands": commands, "sid": sid, "reqId": req_id}])
    body = {"rev": str(rev), "bundles": bundles}
    r = requests.post(
        f"https://docs.google.com/document/u/1/d/{doc_id}/save",
        params=params, data=body, cookies=jar,
        headers={
            "user-agent": "Mozilla/5.0 Chrome/124",
            "x-same-domain": "1",
            "content-type": "application/x-www-form-urlencoded",
        },
        timeout=20,
    )
    text = r.text
    if text.startswith(XSSI):
        text = text[len(XSSI):]
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {text[:300]}")
    return json.loads(text)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("doc")
    ap.add_argument("--rev", type=int, default=1, help="baseline rev (server "
                    "may transform if behind; default 1 is fine for no-op probe)")
    ap.add_argument("--pos", type=int, default=1, help="character index for "
                    "the insert+delete probe (default: 1 = after the title's "
                    "first chunk header)")
    ap.add_argument("--char", default="​", help="character to insert+delete "
                    "(default: zero-width space)")
    ap.add_argument("--real-insert", metavar="TEXT", help="actually insert TEXT "
                    "at --pos (no delete companion). USE WITH CARE.")
    ap.add_argument("--tab", default="t.0")
    args = ap.parse_args()

    doc = normalize_doc(args.doc)
    jar = load_jar()
    ip = info_params(jar, doc)
    sid = make_sid()
    print(f"# token={ip['token'][:12]}...  ouid={ip['ouid']}  sid={sid}", file=sys.stderr)

    if args.real_insert:
        cmds = [{"ty": "is", "ibi": args.pos, "s": args.real_insert}]
    else:
        cmds = [
            {"ty": "is", "ibi": args.pos, "s": args.char},
            {"ty": "ds", "si": args.pos, "ei": args.pos},
        ]

    req_id = int(time.time() * 1000) & 0xFFFFFF
    ack = post_save(jar, doc, sid=sid, token=ip["token"], ouid=ip["ouid"],
                    rev=args.rev, commands=cmds, req_id=req_id, tab=args.tab)
    json.dump(ack, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
