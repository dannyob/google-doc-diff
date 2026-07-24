from google_doc_diff.api import GdocAPI


class _FakeCreds:
    token = "fake-token"


def _api_without_building_clients() -> GdocAPI:
    """GdocAPI.__init__ builds live discovery clients; bypass it."""
    api = GdocAPI.__new__(GdocAPI)
    api._creds = _FakeCreds()
    return api


def _api_with_stubbed_backoff(response):
    """GdocAPI whose _with_backoff returns `response` and records its kwargs."""
    api = _api_without_building_clients()
    captured = {}

    def fake_backoff(factory, **kwargs):
        captured.update(kwargs)
        return response

    class _FakeDocs:
        def documents(self):
            return self

        def get(self, **kwargs):  # never executed; _with_backoff is stubbed
            raise AssertionError("should go through _with_backoff")

    api._docs = _FakeDocs()
    api._with_backoff = fake_backoff
    return api, captured


def test_list_tabs_returns_the_tabs_field():
    tabs = [{"tabProperties": {"tabId": "t.a", "title": "A", "index": 0}}]
    api, _captured = _api_with_stubbed_backoff({"tabs": tabs})
    assert api.list_tabs("DOC123") == tabs


def test_list_tabs_masks_the_response_to_tab_properties():
    """Without a mask this is the full-content call the per-tab path exists to
    avoid; the mask must ask for tabProperties and nothing else."""
    api, captured = _api_with_stubbed_backoff({"tabs": []})
    api.list_tabs("DOC123")

    assert captured["documentId"] == "DOC123"
    assert captured["includeTabsContent"] is True
    fields = captured["fields"]
    assert fields.startswith("tabs(")
    assert "documentTab" not in fields
    assert "body" not in fields


def test_list_tabs_mask_descends_through_child_tabs():
    """Child tabs only appear if the mask names them at each level, so the
    mask must nest deeper than Docs allows tabs to nest (3)."""
    api, captured = _api_with_stubbed_backoff({"tabs": []})
    api.list_tabs("DOC123")
    assert captured["fields"].count("childTabs") >= 3


def test_list_tabs_on_a_document_without_tabs_returns_empty():
    api, _captured = _api_with_stubbed_backoff({})
    assert api.list_tabs("DOC123") == []


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
