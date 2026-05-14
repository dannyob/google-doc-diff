# Kix internals — investigation notes

Probing what the live Google Docs editor ("Kix" is Google's internal codename
for the docs editor) exposes about a document beyond what the public
[Docs](https://developers.google.com/workspace/docs) and
[Drive](https://developers.google.com/workspace/drive) REST APIs surface, and
how reachable those surfaces are from outside the editor.

Captured against the scratch doc
[`1-saoQPcN…tbNDTrwc`](https://docs.google.com/document/d/1-saoQPcNSNXsQXEbKABGUGQ17exdUOj1PhLtbNDTrwc/edit)
in May 2026. Companion probe scripts are in [`../kix_probes/`](../kix_probes/);
captured artifacts live in `kix_probes/data/` (gitignored).

The short version, for impatient readers:

- **The editor's internal document model is just the operational-transformation
  (OT) operation log, reachable as JSON.** No closed-form snapshot; you build
  the document by folding ops. This is more expressive than the public Docs
  API and the same shape both at rest and on the wire.
- **Read and write are reachable without OAuth.** First-party Chrome cookies +
  a token scraped from the `/edit` HTML give you full author access. The OT
  protocol is uniform: the bundles you POST to `/save` look identical to the
  ops you read from `/showrevision`. No /bind / BrowserChannel handshake is
  required for one-shot writes.
- **U+E907 chips are not opaque internally.** Voting chips, smart canvas
  widgets, dropdowns etc. are named sub-models inside the OT stream with
  every emoji, voter, and vote hash named. The public Docs API just hides
  them.
- **Per-revision history is exposed but tile-granular.** `/showrevision` and
  `/revisions/tiles` give you OT-format snapshots and metadata down to the
  same level Drive's revision list exposes. Sub-tile (per-keystroke) history
  is on the `/bind` realtime channel — observable only while it's flowing.
- **The canvas renderer keeps no DOM mirror of the doc body.** Accessibility
  reaches the doc only via the canvas element's `aria-live` events. Anything
  that wants to "read the rendered text" from the page DOM is out of luck.

---

## 1. The OT operation language

Internally everything is a stream of OT ops keyed by `ty` (op type). The same
schema appears in three places: the editor's bootstrap JSON, the
`/showrevision` response, and `/save` request bodies.

| `ty` | meaning | shape |
|---|---|---|
| `mkch` | make chunk header (doc/tab title) | `d: [[ord, "Title"]]` |
| `ac`   | add child (tab) | `d: [tabId, [ord, "Tab name"], [position]]` |
| `is`   | insert string | `ibi: int, s: str` |
| `ds`   | delete string (range) | `si: int, ei: int` (inclusive start, exclusive end) |
| `iss`  | insert string inside a suggestion | `sugid: str, ibi: int, s: str` |
| `msfd` | modify suggestion field | `sugid: str, si: int, ei: int` |
| `ae`   | add element (lists, voting chips, …) | `et: "list"\|"emoji-voting"\|…, id: str, epm: {…}` |
| `as`   | apply style | `st: scope, si: int, ei: int, sm: {…}` |
| `te`   | text-element placement (chip anchor) | `id: str, spi: int` |
| `nm`   | named-sub-model wrapper | `nmr: [kind, id, …], nmc: <inner op or array>` |
| `umv`  | update model version watermark | `mv: int` |
| `null` | no-op (server emits when bundles cancel out) | `{}` |

Style scopes (`st` on `as`) seen so far:

- `headings` — heading-level definitions (`hs_h1`, `hs_h2`, …)
- `paragraph` — paragraph properties (alignment, indent, spacing, page-break-before, …)
- `text` — character runs (font, size, colour, bold/italic/underline/strikethrough/etc.)
- `document` — document-level (dark-mode flags, …)
- `revision_diff` — annotations marking diff regions during version-history rendering

Style maps use a regular two-letter prefix per category: `ts_*` for text,
`ps_*` for paragraph, `hs_*` for headings, `ds_*` for document. The trailing
`_i` on `ts_un_i`, `ps_klt_i` etc. means "inherited" — the property is taking
its value from a parent style rather than this op. Concrete examples:

```
ts_ff "Arial"           — text style: font family
ts_fs 11                — text style: font size (pt)
ts_bd false             — text style: bold
ts_it false             — text style: italic
ts_un false             — text style: underline
ts_st false             — text style: strikethrough
ts_fgc2 { hclr_color: "#000000", clr_type: 0 } — foreground colour
ps_hd 1                 — paragraph style: heading depth (=> H1)
ps_ls 1.0               — paragraph style: line spacing
ps_sa 12.0, ps_sb 12.0  — paragraph: space-after / space-before (pt)
ps_al ...               — paragraph: alignment
```

Suggestions are first-class: every `iss` and `msfd` op carries a stable
`sugid` (`"suggest.mm0ysgr1llgk"` in the scratch doc) which is exactly what
appears under `suggestionId` in the public Docs API. The internal stream
additionally exposes the suggestion's *colour* assignment via
`suggestionColors` on `/showrevision` responses.

Multi-tab docs use the `nm` (named sub-model) wrapper. The first tab's
content sits at the top of the chunk; every other tab is a chain of
`{ty:"nm", nmr:["ksm", "t.tabId"], nmc: <op>}` entries — i.e. each sub-tab's
ops appear nested with a "ksm" (Kix Sub-Model) namespace prefix.

## 2. The internal endpoints

All endpoints live on `https://docs.google.com/document/u/<authuser>/d/<id>/`
(except `/edit` which is also reachable as `/document/d/<id>/edit`).
Authentication is via the user's first-party cookie jar — *not* OAuth
bearer tokens. Several endpoints additionally require a per-page `token` and
`ouid` (user's obfuscated id), both readable directly out of the `/edit`
HTML as:

```js
"info_params":{"token":"AOqKD6…:1778712727467","ouid":"<your-obfuscated-id>",...}
```

### 2.1 `GET /edit` — the bootstrap

The `/edit` page is ~400 KB of HTML containing the bundled UI JS, the feature
flag bag (`window._docs_flag_initialData`), and — most importantly —

```html
<script ...>
DOCS_modelChunk = { "chunk": [ <ops>… ], "revision": <int> };
</script>
```

`DOCS_modelChunk` is the document at boot expressed as OT ops, ready to
be played into the editor. Reading this is the single highest-fidelity
way to get the doc model without touching any other endpoint. The
`revision` field is the chunk's seed rev (typically `1`).

Probe: [`kix_probes/kix_dump_model.py`](../kix_probes/kix_dump_model.py).

### 2.2 `GET /showrevision` — per-revision OT snapshots

```
GET /document/u/1/d/<id>/showrevision
    ?start=<N>&end=<M>&id=<id>
    &smv=2147483647&smb=%5B2147483647%2C+oAMQ%5D
    &srfn=false&ern=false
    &token=<token>&ouid=<ouid>
    &includes_info_params=true&cros_files=false&nded=false&tab=t.0
```

Response is XSSI-prefixed JSON:

```json
{
  "chunkedSnapshot": [ [op,…], [op,…], … ],
  "userInfo":          { "<i>": { name, peopleHovercardId, photo, color, … } },
  "suggestionColors":  { "suggest.<id>": "#RRGGBB", … },
  "nestedDrawingRevisionDiffResults": { … }
}
```

`chunkedSnapshot[0]` is the main tab's ops; `chunkedSnapshot[1..N]` are
per-sub-tab ops wrapped in `{ty:"nm", nmr:["ksm", tabId], nmc:<op>}`. The
chunked array always closes with a final `{ty:"umv", mv:<int>}` marking the
internal model version watermark — useful for "how many OT ops have ever been
applied to this doc?" (separately from the named-revision counter).

Granularity: `start`/`end` are *named-revision* numbers (the same space as
Drive's revisions API), not raw OT-op numbers. For a freshly-created doc that
range is `1..1`; for an active doc it tracks Drive's tile boundaries. There
is no `?revision_diff=…` parameter that gives sub-tile resolution from this
endpoint.

Probe: [`kix_probes/kix_showrevision.py`](../kix_probes/kix_showrevision.py).

### 2.3 `GET /revisions/tiles` — named-revision metadata

```
GET /document/u/1/d/<id>/revisions/tiles
    ?id=<id>&start=1&revisionBatchSize=1500&showDetailedRevisions=true
    &loadType=0&token=…&ouid=…&includes_info_params=true&tab=t.0
```

Response (XSSI-prefixed):

```json
{
  "tileInfo": [
    { "start": 1, "end": 1, "endMillis": 1778711521259,
      "users": ["<ouid>", …], "systemRevs": [],
      "expandable": false, "revisionMac": "Oo8UBZwXcmnzKg" }
  ],
  "userMap":   { "<ouid>": { name, peopleHovercardId, photo, color, anonymous } },
  "firstRev":  1
}
```

This is what powers the Version History UI. `expandable: true` means the
tile can be subdivided into smaller tiles via another `/revisions/tiles`
call with a tighter window. The `revisionMac` is an opaque integrity tag.
Tiles never expose sub-keystroke history.

Probe: covered by `kix_revisions_load.py` (see note in §2.4).

### 2.4 `GET /revisions/load` — vestigial

The 2014-era `/revisions/load?id=<id>&start=N&end=M` endpoint from James
Somers' [reverse-engineering writeup](https://features.jsomers.net/how-i-reverse-engineered-google-docs/)
now returns `HTTP 400` `[["er",…,400,…,3], ["di",…]]` regardless of params.
The current editor uses `/showrevision` instead. Keep the probe script
around as a regression marker.

### 2.5 `POST /save` — the write path

```
POST /document/u/1/d/<id>/save
     ?id=<id>&sid=<16-hex>&vc=1&c=1&w=1&flr=0
     &smv=2147483647&smb=%5B2147483647%2C+oAMQ%5D
     &token=<token>&ouid=<ouid>
     &includes_info_params=true&cros_files=false&nded=false&tab=t.0

Content-Type: application/x-www-form-urlencoded

rev=<int baseline revision>
&bundles=[ { "commands": [<OT op>, …], "sid": "<16-hex>", "reqId": <int> } ]
```

Response (XSSI-prefixed):

```json
{
  "revisionRanges": [[<N>, <N>]],
  "metadata":       { "needsTransformOnClient": false, "serverRevision": <N-1> },
  "ackMessages":   [{
    "c":   [[ <op-after-transform>, <ts_ms>, "<author_ouid>", <rev>,
              "<sid>", <reqId>, null, null, <isUndo:bool> ]],
    "mv":  <model_version>,
    "fv":  <finalized_version>,
    "mfb": [<mv>, "<frontend-build-marker>"],
    "t":   "<ack-token>"
  }]
}
```

Three important facts about `/save`:

1. **The `sid` is not session-scoped.** It's an opaque echo-back tag. Random
   16-hex strings work fine; no `/bind` handshake required. Tested 2026-05.
2. **Bundles that round-trip to a no-op are transformed to `{ty:"null"}` server-side.**
   So `[is "X" at i, ds [i,i]]` is safe to send: it bumps the revision counter
   but leaves the doc text unchanged. Useful as a write-path auth probe.
3. **`needsTransformOnClient: false` after a save means the server cleanly
   appended your ops.** If `true`, you must transform your local ops against
   the intervening server ops before re-trying — i.e. you're implementing
   the client side of operational transformation.

Probe: [`kix_probes/kix_save_probe.py`](../kix_probes/kix_save_probe.py).

### 2.6 `POST /bind` — the realtime channel

`/bind` is a BrowserChannel proto v8 long-poll. URL parameters include
`VER=8, RID=<int>, CVER=1, zx=<rand>, t=<retry>` plus the same
`token`/`ouid` plus a `sid` (allocated by the first POST).

Captured 401 responses on direct curl attempts; the live editor establishes
the channel with a multi-step POST/GET dance (RID-incremented requests,
chunked-encoding streaming reads). Worth noting:

- For *write-only* clients (us, sending /save POSTs), `/bind` is not needed.
- For *receiving* concurrent edits we'd need to actually implement the
  BrowserChannel protocol. The reverse-engineering for that lives at
  [closure-library `goog.net.BrowserChannel`](https://github.com/google/closure-library/blob/master/closure/goog/net/browserchannel.js)
  — which is the same code Google ships in the editor.

A subscriber-only implementation is realistic but is a deeper investment
than fits this pass. For most "track changes since N" use cases, polling
`/showrevision` is easier and good enough.

### 2.7 `GET /comment` — legacy comment overlay

`/document/d/<id>/comment?id=<id>` returns the full HTML of the comment
overlay page. Useful only for screen scraping. The structured comment data
is still best accessed via Drive's v3 Comments API, which is what
`gdoc` already does.

## 3. The U+E907 mystery, solved

Voting chips, emoji reactions, dropdowns and similar smart-canvas widgets
appear in the public Docs API as a `U+E907` placeholder character with no
metadata attached. The OT stream contains the entirety of their state. In the
scratch doc's "Voting Chips" tab, every chip materialises as:

```js
// 1. Anchor element of type "emoji-voting" with a stable kix id:
{ ty:"ae", et:"emoji-voting", id:"kix.escg9h9fzc85", epm:{} }

// 2. Named sub-model attaching populated voter state:
{ ty:"nm",
  nmr:["dtvc", "kix.escg9h9fzc85", false],
  nmc:[ "voting-chip-populate", "➕",
        [ { ui: { ui_oi: "<redacted-voter-ouid>" } } ],    // voter list
        true,                                              // hasCurrentUserVoted
        "AastPo9fpBGWDoGREyxqSHrnjtJHj0Goa7iuNRwmDU6dZX+uJg==" ] }  // signature

// 3. Text-element placement linking the chip to its byte offset:
{ ty:"te", id:"kix.escg9h9fzc85", spi:1 }
```

This recovers strictly more than the markdown-export trick `gdoc` currently
uses for chip count recovery:

- **Exact emoji** (`"➕"`, `"🚀"`, `"👍"`, `"❤️"`) — already in the existing
  output but here without ambiguity.
- **Per-vote voter IDs.** Each voter is `{ui: {ui_oi: "<user_ouid>"}}` — the
  same obfuscated-id space the public People API resolves.
- **`hasCurrentUserVoted` flag.** Tells you whether the authenticated viewer
  has voted — useful for round-trip preservation.
- **Per-vote signature** (`AastPo9…`) — a server-stamped integrity tag.
  Required by `/save` if you ever want to author a chip-vote change.

The `nmr` (named-model-reference) namespace `"dtvc"` is doc-tag-voting-chip;
other smart-canvas widget kinds have their own namespaces (e.g. `"ksm"` for
sub-tab, presumably `"ddpr"` or similar for dropdowns). Captured doc didn't
contain a dropdown to compare against; capture one to round out.

## 4. Revision granularity in practice

There are three counters in play, all distinct:

| Counter | Visible at | Granularity | Addressable |
|---|---|---|---|
| Named revisions | Drive `revisions.list` + `/revisions/tiles` | Drive-decided buckets (named saves, idle gaps, "Show changes" expansion) | yes — `start`/`end` on `/showrevision` |
| Internal OT model version (`umv.mv`) | last op in `/showrevision`, ack body | per applied OT bundle (one keystroke ≈ one bundle) | no — read-only watermark |
| `/save` `rev` baseline | request param | same as model version space | yes (you pick it when posting) |

So you can:

- Pull a complete snapshot at any *named* revision.
- See how many internal ops have occurred (model version watermark).
- Author writes against any baseline revision; the server transforms if
  someone else has edited since.

What you **cannot** do without the `/bind` channel:

- See *intermediate* ops between two named revisions, or recover the order
  of operations within a single named tile. The tile metadata is the floor.
- Get per-keystroke timing for past edits — `endMillis` is per tile.

## 5. The canvas + accessibility surface

Modern Docs renders the document body to `<canvas class="kix-canvas-tile-content">`
elements with no DOM mirror. From the page-context view:

- 1 `contenteditable` element exists in the whole editor — and it's the
  Gemini AI prompt box, not the document body.
- No `role="textbox"` or `role="document"` covers the doc area.
- `document.body.innerText` returns ~800 chars of UI chrome — the actual doc
  text (heading "I. Core Methodologies", body prose) is *not present*
  anywhere in the DOM.
- 10 `aria-live` regions exist; they broadcast individual typed characters
  to assistive tech, but contain no resting copy of the doc.

Consequence: any tool that wants to *observe* the rendered doc from the
running editor has to read the *model* (DOCS_modelChunk → OT stream), not the
DOM. The canvas is opaque both to screen-readers and to scripts.

The surrounding UI chrome (tabs, suggestions, menus, comments sidebar)
*is* in the DOM and labelled with roles — `list`, `tree`, `treeitem`,
`menu`, etc. — so non-body affordances (selecting a tab, opening a
suggestion thread) are scriptable normally.

## 6. End-to-end round-trip in 35 lines of Python

The probe set is sufficient to demonstrate a full round-trip:

```py
import secrets, re, json, requests, browser_cookie3

DOC = "1-saoQPcN…tbNDTrwc"
jar = browser_cookie3.chrome(cookie_file=
    "/Users/<you>/Library/Application Support/Google/Chrome/Profile 1/Cookies",
    domain_name=".google.com")

# Step 1: scrape token + ouid from the editor HTML.
r  = requests.get(f"https://docs.google.com/document/d/{DOC}/edit", cookies=jar,
                  headers={"user-agent": "Mozilla/5.0 Chrome/124"})
ip = json.loads(re.search(r'"info_params"\s*:\s*(\{[^}]+\})', r.text).group(1))

# Step 2: read the full doc as OT ops, parse the embedded bootstrap chunk.
html  = r.text
start = html.find("{", html.index("DOCS_modelChunk = "))
# ... bracket-match to find closing brace; see kix_dump_model.py ...
model = json.loads(html[start:end])
# model["chunk"] is the OT op list; model["revision"] is the seed revision.

# Step 3: write — round-trip safe no-op (insert + matching delete).
sid    = secrets.token_hex(8)
params = {"id": DOC, "sid": sid, "vc":"1","c":"1","w":"1","flr":"0",
          "smv":"2147483647","smb":"[2147483647, oAMQ]",
          "token": ip["token"], "ouid": ip["ouid"],
          "includes_info_params":"true","cros_files":"false",
          "nded":"false","tab":"t.0"}
body   = {"rev":"1",
          "bundles": json.dumps([{
            "commands": [{"ty":"is","ibi":1,"s":"​"},
                         {"ty":"ds","si":1,"ei":1}],
            "sid": sid, "reqId": 1}])}
ack = requests.post(f"https://docs.google.com/document/u/1/d/{DOC}/save",
                    params=params, data=body, cookies=jar,
                    headers={"user-agent":"Mozilla/5.0 Chrome/124",
                             "x-same-domain":"1",
                             "origin":"https://docs.google.com"})
print(ack.text)
# => )]}'\n{"revisionRanges":[[N,N]], "metadata":..., "ackMessages":[{"c":[[{"ty":"null"},...]]}]}
```

## 7. Implications for `gdoc` v2

Pulling all of this together for the round-trip-to-Markdown goal:

| What | How |
|---|---|
| **Full doc model** (every property the editor sees) | `kix_dump_model.py` + recursive op-fold. Gives us each named tab, every chip's emoji + voter list + signature, every paragraph and text style, every list and table, every suggestion (with stable id), every comment anchor. |
| **History deeper than Drive's `revisions.list`** | `kix_revisions_load.py --probe-max` to find the named-revision ceiling, then `kix_showrevision.py --start N --end N` per tile. Gives OT ops not just rendered HTML, so we can replay every named revision into a synthetic doc rather than relying on Drive's compaction-dropped intermediate revs. |
| **Round-trip writes** (markdown changes → live doc) | Parse markdown back into OT ops (existing `is`/`ds`/`as`/`ae`/`nm` vocabulary), POST a single `/save` per bundle, retry on `needsTransformOnClient: true`. Keeps suggestion ids, comment anchors, and chip widgets intact because we send the *same* op vocabulary the editor uses internally. |
| **Authoring suggestions** | Use `iss` with a freshly-allocated `sugid` (the editor uses `suggest.<random>`) instead of `is`, paired with `msfd` for range-edits. The Docs API can read suggestions but can't *author* them as suggestions — `/save` can. |
| **Authoring chips** | `ae` with the right `et` + a `nm` with the chip's namespace (`dtvc` for voting, etc.) + a `te` to anchor it. The signature field is server-stamped on first write, so you only need to keep it for re-writes. |

Things still off the table:

- **Drawings.** Not in the OT stream we capture. Probably live in a
  separate Drawings sub-model fetched on-demand.
- **Real-time subscription.** Receiving concurrent edits without polling
  requires implementing `goog.net.BrowserChannel`.
- **Doc IDs prior to compaction.** Drive's `/revisions` truncation isn't
  fixable by going through Kix — the same data is gone from both surfaces.
- **OAuth-only access.** All of the above assumes browser-cookie access.
  OAuth bearer tokens still buy you only the public Docs/Drive APIs.

## 8. Probe scripts

All standalone (PEP 723) — `uv run --script` adds deps on first run.

| Script | Purpose |
|---|---|
| `kix_cookies.py` | Read docs.google.com cookies from Chrome (Profile auto-selected by mtime; override with `KIX_CHROME_PROFILE=…`). |
| `kix_dump_model.py` | Fetch `/edit`, extract `DOCS_modelChunk`, summarise or dump JSON. |
| `kix_showrevision.py` | Hit `/showrevision` for a named revision range; returns OT op chunks. |
| `kix_revisions_load.py` | Hit the (now-broken) legacy `/revisions/load` — kept as regression marker. |
| `kix_save_probe.py` | POST a single bundle to `/save`. Default mode does a round-trip-safe no-op; `--real-insert TEXT` actually inserts. |

Each uses the same cookie loader and shares the `info_params` scraper logic.

## 9. References

- [How I reverse-engineered Google Docs to play back any document's keystrokes — James Somers, 2014](https://features.jsomers.net/how-i-reverse-engineered-google-docs/) — original write-up. Endpoints have changed (`/revisions/load` → `/showrevision`); operation grammar essentially the same.
- [`goog.net.BrowserChannel`](https://github.com/google/closure-library/blob/master/closure/goog/net/browserchannel.js) — the realtime transport, same code Kix bundles.
- Internal kix CSS classnames (`kix-page-paginated`, `kix-canvas-tile-content`, `kix-cursor-caret`, `kix-rotatingtilemanager`, …) for the editor surface.
