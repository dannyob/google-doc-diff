"""Tab enumeration for documents whose bulk `documents.get` call fails.

`documents.get?includeTabsContent=true` 500s on large multi-tab docs, and it
is the only API route to the tab list -- there is no per-tab variant and a
field mask does not help. The `/edit` payload, which the OAuth bearer alone
can fetch, embeds the same information as one op per tab:

    {"ty":"ac","d":["t.av9h4hz2va7o",[1,"2026-05-06"],[10]]}
                     tab id            title            order

`kix/model.py` parses the same payload for its OT stream, but authenticates
with Chrome cookies and lives behind the optional [kix] extra. This module
shares the idiom, not the dependency.
"""

import json
import re
from dataclasses import dataclass

_AC_START = re.compile(r'\{"ty":"ac","d":\[')
_DECODER = json.JSONDecoder()


@dataclass(frozen=True)
class TabRef:
    """A tab's identity as advertised by the editor payload."""

    tab_id: str  # Google's raw id, e.g. 't.av9h4hz2va7o'
    title: str
    index: int  # display order


def parse_tab_refs(html: str) -> list[TabRef]:
    """Extract the document's tabs from an `/edit` payload, in display order."""
    refs: dict[str, TabRef] = {}
    for m in _AC_START.finditer(html):
        try:
            obj, _end = _DECODER.raw_decode(html, m.start())
            d = obj.get("d")
            if not isinstance(d, list) or len(d) < 3:
                continue
            tab_id, title_field, index_field = d[0], d[1], d[2]
            if not isinstance(tab_id, str) or not tab_id.startswith("t."):
                continue
            title = ""
            if isinstance(title_field, list) and len(title_field) > 1:
                title = str(title_field[1])
            index = 0
            if isinstance(index_field, list) and index_field:
                index = int(index_field[0])
        except (ValueError, TypeError):
            # This format is reverse-engineered from one observed op shape;
            # an op that doesn't match it should be skipped, not fatal.
            continue
        refs[tab_id] = TabRef(tab_id=tab_id, title=title, index=index)
    return sorted(refs.values(), key=lambda r: r.index)
