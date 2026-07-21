# Per-tab pull for large multi-tab docs

Issue #1. Design date 2026-07-20.

## Problem

`gdoc pull` fails with HTTP 500 on large multi-tab documents. The failing call
is the one every read path makes:

```
GET https://docs.googleapis.com/v1/documents/<id>?includeTabsContent=true
-> 500 "Internal error encountered."
```

Reproduced against the 3.4 MB "FOC Pod WBR/ORR" doc
(`1gnUazQiQ7KdxcKtQBL190TskZFYN5RFRlchWwKeu50g`), which has 24 tabs.

### What the API actually allows

The issue as filed proposed fetching one tab at a time through the Docs API.
That is not possible. `documents.get` accepts exactly three query parameters —
`suggestionsViewMode`, `includeTabsContent`, `commentsViewMode` — and none of
them selects a tab. Probes against the failing doc:

| Request | Result |
|---|---|
| `includeTabsContent=true` | 500 after 3.8s |
| `includeTabsContent=true&fields=tabs/tabProperties` | 500 — a field mask does not help; the server assembles the whole document before masking |
| `includeTabsContent=true` with each of the three `suggestionsViewMode` values | 500 — not suggestion expansion |
| `includeTabsContent=false` | 200 in 0.6s, first tab only |
| `gog docs list-tabs` | 500 — it makes the same call |

So the high-fidelity JSON is unavailable for this document at any granularity.

### What does work

The undocumented per-tab export:

```
GET https://docs.google.com/document/d/<id>/export?format=md&tab=<tabId>
```

`t.0` returned 6.7 KB, `t.tmxtfruhgila` returned 150 KB, with distinct content.
This is the lossy `from_google_md.py` road: no suggestions, no stable paragraph
IDs. Drive's comment API is unaffected by document size, so comments can be
recovered separately — hence "hybrid".

Tab IDs, titles, and ordering come from the `/edit` payload, which serves 200
to the OAuth bearer token alone. It carries one op per tab:

```json
{"ty":"ac","d":["t.av9h4hz2va7o",[1,"2026-05-06"],[10]]}
```

24 of these were recovered from the failing doc, giving id, title, and index.

## Design

### Tab enumeration — `tabs.py`

A pure `parse_tab_refs(html) -> list[TabRef]` where `TabRef` holds `tab_id`,
`title`, and `index`, fed by a new `api.fetch_edit_html(doc_id)`.

`kix/model.py` already fetches and parses the same `/edit` chunks, but it
authenticates with Chrome cookies and lives behind the optional `[kix]` extra.
Routing large-doc pulls through it would make them depend on a browser profile.
The new module shares the idiom, not the dependency.

### Assembly — `per_tab.py`

`api.export_tab_markdown(doc_id, tab_id)` fetches the per-tab export through the
existing `_do_get` / `_with_backoff_http` pair, which already retries 429 and
5xx with exponential backoff.

`per_tab.py` runs each tab's markdown through `build_from_google_md`, assembles
a single `Document` carrying the real tab IDs, titles, and order, then fetches
`list_comments` and anchors it with the existing `anchor_comments.py`. The
result sets `comments_preserved=True` and `suggestions_preserved=False`.

Tabs are fetched sequentially with a one-second delay between requests, on top
of the backoff that `_with_backoff_http` applies when a request is refused. The
export endpoint rate-limits aggressively: eight-way parallel fetching exhausted
the quota, after which even serial requests returned 429 for most tabs. A pull
of a 24-tab doc takes minutes.

### Trigger

`pull` attempts the rich bulk call first. On a 500 it prints a loud warning that
fidelity is degraded and falls back to the per-tab path. `--per-tab` forces the
fallback; `--no-per-tab` disables it so the 500 surfaces as an error.

Documents that pull fine today are unaffected.

### Safeguards

Two failure modes were observed and both are checked:

1. **Silent fallback.** An unrecognized tab ID does not error — it returns the
   default tab's content with status 200. Hashing each tab's exported bytes
   catches this without any extra request: if a bad ID returns another tab's
   content, the two hashes collide. Duplicate hashes abort the pull, naming the
   offending tab titles.
2. **Error pages.** A throttled request returns an HTML error page. `_do_get`
   already rejects it on status, and `per_tab.py` additionally rejects bodies
   that are HTML-shaped rather than markdown.

Both abort rather than warn. A genuine pair of byte-identical tabs is rare, and
a loud error naming the titles lets a human decide.

## Resolved: `t.0` is an alias, not a 25th tab

`t.0` exports a 6.7 KB intro ("FOC Weekly Business Review (WBR) / ORR") but does
not appear among the 24 `ac` ops, raising the possibility of a tab this design
would silently drop. Settled against the `/edit` payload, where content chunks
are routed to a tab by `"nmr":["ksm","<tabId>"]`:

- 24 tabs appear in `ac` ops; 24 tabs own content chunks; the two sets are
  identical, with no member on either side only.
- The intro text belongs to `t.90cn325iwsp4`, titled "[Updated] Template",
  which is in the `ac` list.

So the `ac` ops are the complete tab set, and `t.0` is an alias for a tab
already covered. Enumeration must not add `t.0` on top of the `ac` list — doing
so would duplicate that tab, which is precisely what the duplicate-hash
safeguard is there to catch.

## Testing

Following the existing conventions: mocked against fixtures, no network.

- `parse_tab_refs` against a trimmed `/edit` fixture, including the nested-op
  and missing-title cases.
- Duplicate-hash detection and HTML-body rejection.
- Assembly of several per-tab markdown fixtures into one multi-tab `Document`,
  checking tab IDs, titles, and order survive.
- A round-trip test through the mocked pipeline confirming the emitted
  `::: {.gd-tab data-title="..."}` fenced divs match the enumerated tabs and
  that comments anchor.

Per the repo's rule, the `gdoc` binary gets rebuilt and smoke-tested against the
live FOC doc before this is called done.

## Not in scope

Recovering suggestions or stable paragraph IDs for these documents. Google does
not expose them at any granularity once the bulk call fails.
