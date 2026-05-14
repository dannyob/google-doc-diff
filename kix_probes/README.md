# Kix probe scripts

Standalone PEP 723 scripts (`uv run --script`) for probing the closed-but-reachable
surfaces of the live Google Docs editor ("Kix"). They live outside the main
`gdoc` package because they use *cookie* authentication, not OAuth, and they
talk to internal endpoints that may break at any time.

## Auth model

The Docs editor authenticates with first-party cookies on `docs.google.com`
(`SID`, `__Secure-1PSID`, `HSID`, `SSID`, `APISID`, `SAPISID`, `__Secure-1PAPISID`,
plus the XSRF token derived from `SAPISID` at request time). OAuth bearer tokens
do **not** work for these endpoints.

`kix_cookies.py` reads the live cookies out of Chrome's profile (macOS
keychain-protected). All other scripts call it.

## What each script does

| Script | Endpoint | What you learn |
|---|---|---|
| `kix_cookies.py` | n/a | Extract docs.google.com cookies from Chrome |
| `kix_dump_model.py` | `GET /edit` | The full bootstrap OT op log (`DOCS_modelChunk`) |
| `kix_revisions_load.py` | `GET /revisions/load` | Per-revision OT chunks from rev N to M |
| `kix_revisions_tiles.py` | `GET /revisions/tiles` | Named-revision metadata + thumbnails |
| `kix_bind_open.py` | `POST /bind` | Open the realtime channel and dump messages |
| `kix_mutate.py` | `POST /mutate` | Send a single OT op (e.g. insert a character) |
| `kix_comments.py` | `GET /discussion/.../comments` | Internal comment API (incl. drafts) |

## Caveats

- These endpoints are **undocumented and unstable**. They have changed in the
  past and will again. Don't ship anything that depends on them without a
  fallback to the public Docs API.
- The `bind` channel is BrowserChannel/GFE protocol v8. Long-polling.
- Writing via `/mutate` will increment the doc's revision counter — visible to
  any other editor and to Drive's revision history.
