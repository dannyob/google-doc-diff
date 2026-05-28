"""Op-plan apply backends + channel-selection policy.

`policy.channel_for(op)` picks a write channel per op:
  - DOCS_API:  Google Docs `batchUpdate`
  - DRIVE_API: Drive Comments v3
  - KIX_SAVE:  the internal `/save` POST (covered in kix_probes/)

`docs_api.translate(ops, block_index)` turns each `Op` into a Docs API
`Request` dict; `docs_api.apply(plan, doc_id, service, ...)` actually calls
batchUpdate.

Overnight scope: only `docs_api` is implemented; `drive_api` and `kix_save`
stubs exist so the dispatcher's table is complete.
"""
from google_doc_diff.apply.policy import (
    DOCS_API,
    DRIVE_API,
    KIX_SAVE,
    Channel,
    channel_for,
    group_by_channel,
)

__all__ = ["Channel", "DOCS_API", "DRIVE_API", "KIX_SAVE", "channel_for", "group_by_channel"]
