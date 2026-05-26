"""Tests for kix.model — OT op extraction from /edit HTML."""

from google_doc_diff.kix.model import KixModel, extract_ot_ops


MINIMAL_HTML = """
<html><head></head><body>
<script nonce="abc">
DOCS_modelChunk = {"chunk":[{"ty":"mkch","d":[[1,"Test Doc"]]},{"ty":"is","ibi":1,"s":"Hello"},{"ty":"umv","mv":42}],"revision":1};
</script>
</body></html>
"""

NO_CHUNK_HTML = """
<html><head></head><body>
<p>Sign in to continue</p>
</body></html>
"""

NESTED_BRACES_HTML = """
<html><body>
<script>
DOCS_modelChunk = {"chunk":[{"ty":"as","st":"text","si":0,"ei":5,"sm":{"ts_fgc2":{"hclr_color":"#000000","clr_type":0}}}],"revision":3,"suggestionColors":{"suggest.x":"#ff0000"}};
</script>
</body></html>
"""


def test_extract_minimal():
    model = extract_ot_ops(MINIMAL_HTML)
    assert model is not None
    assert isinstance(model, KixModel)
    assert model.revision == 1
    assert model.model_version == 42
    assert len(model.ops) == 3
    assert model.ops[0]["ty"] == "mkch"
    assert model.ops[1]["ty"] == "is"
    assert model.ops[1]["s"] == "Hello"


def test_extract_no_chunk_returns_none():
    model = extract_ot_ops(NO_CHUNK_HTML)
    assert model is None


def test_extract_nested_braces():
    model = extract_ot_ops(NESTED_BRACES_HTML)
    assert model is not None
    assert model.revision == 3
    assert model.ops[0]["ty"] == "as"
    assert model.ops[0]["sm"]["ts_fgc2"]["hclr_color"] == "#000000"
    assert model.suggestion_colors == {"suggest.x": "#ff0000"}


def test_model_version_absent_defaults_to_revision():
    html = """
    <script>
    DOCS_modelChunk = {"chunk":[{"ty":"is","ibi":1,"s":"x"}],"revision":7};
    </script>
    """
    model = extract_ot_ops(html)
    assert model is not None
    assert model.model_version == 7
