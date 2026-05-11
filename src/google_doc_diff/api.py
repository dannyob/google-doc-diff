"""Google Docs / Drive API wrappers with rate-limit handling.

`GdocAPI` builds Drive v2, Drive v3, and Docs v1 service handles. Every API
call goes through `_with_backoff`, which retries on 429 with exponential
backoff + jitter (1, 2, 4, 8, max 60s; up to 5 retries).

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

    def _do_get(self, url: str) -> bytes:
        headers = {
            "Authorization": f"Bearer {self._creds.token}",
            "User-Agent": USER_AGENT,
        }
        r = requests.get(url, headers=headers, timeout=30)
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
        exponential backoff on 429."""
        last: BaseException | None = None
        for attempt in range(5):
            try:
                return factory(*args, **kwargs).execute()
            except HttpError as e:
                if getattr(e, "status_code", None) == 429 or e.resp.status == 429:
                    last = e
                    self._sleep_for_attempt(attempt)
                    continue
                raise
        raise APIError("too many 429s; gave up after 5 attempts") from last

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


class _Transient(Exception):
    def __init__(self, status_code: int, retry_after: str | None = None):
        self.status_code = status_code
        self.retry_after = retry_after
