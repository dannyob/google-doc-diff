from google_doc_diff.api import GdocAPI


class _FakeCreds:
    token = "fake-token"


def _api_without_building_clients() -> GdocAPI:
    """GdocAPI.__init__ builds live discovery clients; bypass it."""
    api = GdocAPI.__new__(GdocAPI)
    api._creds = _FakeCreds()
    return api


def test_fetch_edit_html_hits_the_edit_url_and_decodes():
    api = _api_without_building_clients()
    seen = []
    api._do_get = lambda url: seen.append(url) or b"<html>hi</html>"

    assert api.fetch_edit_html("DOC123") == "<html>hi</html>"
    assert seen == ["https://docs.google.com/document/d/DOC123/edit"]


def test_export_tab_markdown_passes_the_tab_parameter():
    api = _api_without_building_clients()
    seen = []
    api._do_get = lambda url: seen.append(url) or b"# tab\n"

    assert api.export_tab_markdown("DOC123", "t.abc") == "# tab\n"
    assert seen == [
        "https://docs.google.com/document/d/DOC123/export?format=md&tab=t.abc"
    ]


def test_export_tab_markdown_retries_through_the_backoff_helper():
    """The export endpoint 429s under load; retries must go through
    _with_backoff_http rather than a second ad-hoc retry path."""
    api = _api_without_building_clients()
    calls = []

    def fake_backoff(fn, *args):
        calls.append((fn, args))
        return b"# tab\n"

    api._with_backoff_http = fake_backoff
    api.export_tab_markdown("DOC123", "t.abc")
    assert len(calls) == 1
    assert calls[0][0] == api._do_get


def test_get_document_metadata_asks_for_no_tab_content():
    api = _api_without_building_clients()
    captured = {}

    def fake_backoff(factory, **kwargs):
        captured.update(kwargs)
        return {"title": "Doc", "revisionId": "rev9"}

    class _FakeDocs:
        def documents(self):
            return self

        def get(self, **kwargs):  # never executed; _with_backoff is stubbed
            raise AssertionError("should go through _with_backoff")

    api._docs = _FakeDocs()
    api._with_backoff = fake_backoff
    assert api.get_document_metadata("DOC123")["revisionId"] == "rev9"
    assert captured["includeTabsContent"] is False
    assert captured["documentId"] == "DOC123"
