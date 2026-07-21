"""Google Docs / Drive API wrappers with rate-limit handling.

`GdocAPI` builds Drive v2, Drive v3, and Docs v1 service handles. Every API
call goes through `_with_backoff`, which retries rate-limit errors (429, or
403 with a usageLimits rateLimitExceeded reason — Drive uses both) with
exponential backoff + jitter (1, 2, 4, 8, max 60s; up to 5 retries).

`fetch_revision_export` uses the Drive v2 `exportLinks` URLs (returned by
`revisions.list`) — verified live on 2026-05-09. Per-revision content is not
exposed by Drive v3 for native Google Docs.
"""

from __future__ import annotations

import random
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

USER_AGENT = "gdoc/0.1.0 (google-doc-diff)"


class APIError(RuntimeError):
    pass


_BARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")


def drive_url_for(doc_id: str) -> str:
    """Canonical Drive URL for a Google Doc by ID. Inverse of `parse_doc_id`."""
    return f"https://docs.google.com/document/d/{doc_id}/edit"


def parse_doc_id(s: str) -> str:
    """Accept either a bare doc ID or a Google Docs / Drive URL.

    Strips the `/edit?...` portion and any tab query params.
    """
    if _BARE_ID_RE.match(s):
        return s
    parsed = urlparse(s)
    m = re.search(r"/document/d/([A-Za-z0-9_-]+)", parsed.path)
    if m:
        return m.group(1)
    m = re.search(r"/d/([A-Za-z0-9_-]+)", parsed.path)
    if m:
        return m.group(1)
    qs = parse_qs(parsed.query)
    if "id" in qs:
        return qs["id"][0]
    raise APIError(f"could not extract doc ID from {s!r}")


class GdocAPI:
    def __init__(self, credentials):
        self._creds = credentials
        self._drive_v2 = build("drive", "v2", credentials=credentials, cache_discovery=False)
        self._drive_v3 = build("drive", "v3", credentials=credentials, cache_discovery=False)
        self._docs = build("docs", "v1", credentials=credentials, cache_discovery=False)

    @property
    def access_token(self) -> str | None:
        return getattr(self._creds, "token", None)

    # -- core API calls -----------------------------------------------------

    def get_document(self, doc_id: str) -> dict:
        """Fetch the full Docs API JSON for the current revision, with tabs."""
        return self._with_backoff(
            self._docs.documents().get,
            documentId=doc_id,
            includeTabsContent=True,
        )

    def list_revisions(self, doc_id: str) -> list[dict]:
        """List Drive v2 revisions with exportLinks."""
        revisions: list[dict] = []
        page_token = None
        while True:
            resp = self._with_backoff(
                self._drive_v2.revisions().list,
                fileId=doc_id,
                fields=(
                    "items(id,modifiedDate,lastModifyingUser(emailAddress,displayName),"
                    "exportLinks),nextPageToken"
                ),
                pageToken=page_token,
            )
            revisions.extend(resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return revisions

    def list_comments(self, doc_id: str) -> list[dict]:
        """List Drive v3 comments + replies with the fields the AST needs."""
        comments: list[dict] = []
        page_token = None
        fields = (
            "comments("
            "id,createdTime,modifiedTime,author,content,htmlContent,"
            "anchor,quotedFileContent,resolved,deleted,"
            "replies(id,createdTime,modifiedTime,author,content,action,deleted)"
            "),nextPageToken"
        )
        while True:
            resp = self._with_backoff(
                self._drive_v3.comments().list,
                fileId=doc_id,
                fields=fields,
                includeDeleted=True,
                pageToken=page_token,
            )
            comments.extend(resp.get("comments", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return comments

    def fetch_revision_export(self, export_url: str) -> bytes:
        """Fetch a per-revision export URL (from revisions.list exportLinks)."""
        return self._with_backoff_http(self._do_get, export_url)

    def fetch_edit_html(self, doc_id: str) -> str:
        """Fetch the /edit payload, which carries the tab list.

        Used by the per-tab pull path: `documents.get?includeTabsContent=true`
        is the only API route to the tab list and it 500s on large docs, but
        /edit serves 200 to the OAuth bearer alone (no browser cookies).
        """
        url = f"https://docs.google.com/document/d/{doc_id}/edit"
        return self._with_backoff_http(self._do_get, url).decode("utf-8", errors="replace")

    def export_tab_markdown(self, doc_id: str, tab_id: str) -> str:
        """Export a single tab as markdown.

        The `tab=` parameter is undocumented and could change. Note that an
        unrecognised tab id does NOT error -- it returns the default tab's
        content with status 200 -- so callers must check for duplicates.
        """
        url = (
            f"https://docs.google.com/document/d/{doc_id}"
            f"/export?format=md&tab={tab_id}"
        )
        return self._with_backoff_http(self._do_get, url).decode("utf-8", errors="replace")

    def get_document_metadata(self, doc_id: str) -> dict:
        """Fetch title/revisionId without tab content.

        Returns in under a second on documents whose includeTabsContent=true
        call 500s, so the per-tab path can still label its output correctly.
        """
        return self._with_backoff(
            self._docs.documents().get,
            documentId=doc_id,
            includeTabsContent=False,
        )

    def _do_get(self, url: str) -> bytes:
        headers = {
            "Authorization": f"Bearer {self._creds.token}",
            "User-Agent": USER_AGENT,
        }
        try:
            # Markdown/HTML exports of large, busy docs can take well over
            # 30s to render server-side before the first byte arrives.
            r = requests.get(url, headers=headers, timeout=180)
        except (requests.Timeout, requests.ConnectionError) as e:
            raise _Transient(599) from e
        if r.status_code == 429 or 500 <= r.status_code < 600:
            # Google's export endpoint flakes with transient 5xx errors;
            # treat them like rate-limiting and retry.
            raise _Transient(r.status_code, r.headers.get("Retry-After"))
        if r.status_code >= 400:
            raise APIError(f"HTTP {r.status_code} from {url}: {r.text[:200]}")
        return r.content

    # -- backoff retry loops -----------------------------------------------

    def _with_backoff(self, factory, *args, **kwargs) -> Any:
        """Wrap a googleapiclient call (factory(*args, **kwargs).execute()) with
        exponential backoff on rate-limit errors."""
        last: BaseException | None = None
        for attempt in range(5):
            try:
                return factory(*args, **kwargs).execute()
            except HttpError as e:
                if _is_rate_limit(e):
                    last = e
                    self._sleep_for_attempt(attempt)
                    continue
                raise
        raise APIError("rate-limited; gave up after 5 attempts") from last

    def _with_backoff_http(self, fn, *args) -> bytes:
        last: BaseException | None = None
        for attempt in range(5):
            try:
                return fn(*args)
            except _Transient as e:
                last = e
                self._sleep_for_attempt(attempt)
        status = getattr(last, "status_code", "?")
        raise APIError(
            f"raw HTTP fetch gave up after 5 attempts (last status {status})"
        ) from last

    @staticmethod
    def _sleep_for_attempt(attempt: int) -> None:
        base = min(60, 2 ** attempt)         # 1, 2, 4, 8, 16, 32, 60...
        jitter = random.uniform(0, 0.5 * base)
        time.sleep(base + jitter)


def _is_rate_limit(e: HttpError) -> bool:
    """Drive reports rate limiting as 429, or as 403 with a usageLimits
    reason (rateLimitExceeded / userRateLimitExceeded)."""
    status = getattr(e, "status_code", None) or e.resp.status
    if status == 429:
        return True
    return status == 403 and b"ateLimitExceeded" in (e.content or b"")


class _Transient(Exception):
    def __init__(self, status_code: int, retry_after: str | None = None):
        self.status_code = status_code
        self.retry_after = retry_after
