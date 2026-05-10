"""Tests for api.parse_doc_id (the rest of api.py needs live API or heavy mocking)."""

import pytest

from google_doc_diff.api import APIError, drive_url_for, parse_doc_id


@pytest.mark.parametrize("inp,expected", [
    ("1aBcDeFGhIjKLMNoPqRsTuVwXyZ_example_id_1234",
     "1aBcDeFGhIjKLMNoPqRsTuVwXyZ_example_id_1234"),
    ("https://docs.google.com/document/d/1aBcDeFGhIjKLMN/edit",
     "1aBcDeFGhIjKLMN"),
    ("https://docs.google.com/document/d/1aBcDeFGhIjKLMN/edit?tab=t.0",
     "1aBcDeFGhIjKLMN"),
    ("https://docs.google.com/document/d/1aBcDeFGhIjKLMN/edit?tab=t.etklgkun8k5h#heading=h.x",
     "1aBcDeFGhIjKLMN"),
    ("https://drive.google.com/file/d/SHORT_ID/view",
     "SHORT_ID"),
])
def test_parse_doc_id(inp, expected):
    assert parse_doc_id(inp) == expected


def test_parse_doc_id_raises_on_garbage():
    with pytest.raises(APIError):
        parse_doc_id("not a url and too short")


def test_drive_url_for_canonical():
    assert drive_url_for("ABC123") == "https://docs.google.com/document/d/ABC123/edit"
