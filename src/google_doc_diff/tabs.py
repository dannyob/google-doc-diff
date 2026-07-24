"""Tab enumeration for documents whose bulk `documents.get` call is too big.

`documents.get?includeTabsContent=true` returns every tab's full content, which
is exactly what the per-tab path is trying to avoid. A field mask trims the
response to tab identity alone:

    tabs(tabProperties,childTabs(tabProperties,childTabs(...)))

That is a supported API route, it carries the real child-tab tree, and it is
markedly cheaper than the unmasked call -- 7.2s vs 18.9s on a 13.5MB, 57-tab
document. `api.GdocAPI.list_tabs` issues it; this module turns the response
into `TabRef`s.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TabRef:
    """A tab's identity, without its content."""

    tab_id: str  # Google's raw id, e.g. 't.av9h4hz2va7o'
    title: str
    index: int  # display order among its siblings
    level: int = 0  # 0 = top-level
    parent_tab_id: str | None = None
    children: tuple[TabRef, ...] = field(default=())


def tab_refs_from_json(
    tabs: Iterable[dict] | None, *, skipped: list[str] | None = None
) -> list[TabRef]:
    """Build the tab tree from the `tabs` field of a masked `documents.get`.

    Siblings are ordered by `tabProperties.index`, falling back to their
    position in the response when the field is absent (the API omits it for
    index 0). Tabs without a `tabId` are skipped: the export endpoint answers
    an unaddressable tab with the default tab's content and a 200, so a ref
    with an empty id would silently duplicate another tab. `skipped`, if given,
    collects their titles so a lost tab isn't mistaken for a tab that wasn't
    there.
    """
    return _refs(tabs, level=0, parent_id=None, skipped=skipped)


def _refs(
    tabs: Iterable[dict] | None,
    *,
    level: int,
    parent_id: str | None,
    skipped: list[str] | None,
) -> list[TabRef]:
    ordered = sorted(
        enumerate(tabs or []),
        key=lambda pair: (pair[1].get("tabProperties", {}).get("index", pair[0])),
    )
    refs = []
    for _position, tab in ordered:
        props = tab.get("tabProperties") or {}
        tab_id = props.get("tabId")
        if not tab_id:
            if skipped is not None:
                skipped.append(props.get("title") or "(untitled)")
            continue
        refs.append(TabRef(
            tab_id=tab_id,
            title=props.get("title", ""),
            index=props.get("index", 0),
            level=level,
            parent_tab_id=parent_id,
            children=tuple(_refs(
                tab.get("childTabs"),
                level=level + 1,
                parent_id=tab_id,
                skipped=skipped,
            )),
        ))
    return refs


def walk_tab_refs(refs: Iterable[TabRef]) -> Iterator[TabRef]:
    """Yield every ref depth-first, in document order."""
    for ref in refs:
        yield ref
        yield from walk_tab_refs(ref.children)
