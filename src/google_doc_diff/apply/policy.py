"""Channel-selection policy.

One dispatcher, one table. The spec's "channel_for" function maps each
OpPlan primitive (and, where ambiguous, its data) onto exactly one
backend channel. Tests assert the mapping; the apply pipeline groups ops
by channel before sending.
"""
from __future__ import annotations

from collections import defaultdict
from enum import Enum

from google_doc_diff.ops.primitives import Op


class Channel(str, Enum):
    DOCS_API = "docs_api"
    DRIVE_API = "drive_api"
    KIX_SAVE = "kix_save"


DOCS_API = Channel.DOCS_API
DRIVE_API = Channel.DRIVE_API
KIX_SAVE = Channel.KIX_SAVE


def channel_for(op: Op) -> Channel:
    """Choose a write channel for a single primitive.

    Overnight scope is "Docs API for everything"; the dispatcher exists so
    later additions (chip authoring, suggestion authoring, comment lifecycle)
    can route to KIX_SAVE / DRIVE_API without changing every callsite.
    """
    return DOCS_API


def group_by_channel(plan) -> dict[Channel, list[Op]]:
    """Bucket ops by their chosen channel, preserving in-channel order."""
    out: dict[Channel, list[Op]] = defaultdict(list)
    for op in plan:
        out[channel_for(op)].append(op)
    return dict(out)
