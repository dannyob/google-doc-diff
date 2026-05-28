"""Tests for kix.model — OT op extraction from /edit HTML.

Real /edit pages serialize the model as many ``DOCS_modelChunk = {...}``
blocks (interleaved with ``= undefined;`` resets). The document content ops
(``is``/``as``/``ae``/``te``/``iss``) are *wrapped* one-per ``nm`` op routed to
a tab via ``nmr == ["ksm", TAB_ID]``; the real op is the ``nmc`` payload.
Top-level structural ops (``mkch``/``umv``/``dc``…) are not tab content.
"""

from google_doc_diff.kix.model import KixModel, extract_ot_ops

# Two chunk blocks, two tabs, ksm-wrapped content + a top-level umv watermark.
WRAPPED_HTML = """
<html><body>
<script nonce="abc">DOCS_modelChunk = undefined;</script>
<script nonce="abc">
DOCS_modelChunk = {"chunk":[
  {"ty":"mkch","d":[[1,"Test Doc"]]},
  {"ty":"nm","nmr":["ksm","t.0"],"nmc":{"ty":"is","ibi":1,"s":"Hello"}},
  {"ty":"nm","nmr":["ksm","t.0"],"nmc":{"ty":"as","st":"doco_anchor","si":1,"ei":3,"sm":{"das_a":{"cv":{"op":"set","opValue":["kix.c1"]}}}}},
  {"ty":"nm","nmr":["ksm","t.1"],"nmc":{"ty":"is","ibi":1,"s":"Second tab"}},
  {"ty":"umv","mv":42}
],"revision":5};
</script>
<script nonce="abc">DOCS_modelChunk = undefined;</script>
<script nonce="abc">
DOCS_modelChunk = {"chunk":[
  {"ty":"nm","nmr":["ksm","t.0"],"nmc":{"ty":"ae","et":"emoji-voting","id":"kix.chip1","epm":{}}}
],"revision":6,"suggestionColors":{"suggest.x":"#ff0000"}};
</script>
</body></html>
"""

NO_CHUNK_HTML = """
<html><head></head><body>
<p>Sign in to continue</p>
</body></html>
"""


def test_extract_unwraps_ksm_per_tab():
    model = extract_ot_ops(WRAPPED_HTML)
    assert model is not None
    assert isinstance(model, KixModel)
    assert set(model.ops_by_tab) == {"t.0", "t.1"}
    # t.0: is + as (chunk 1) + ae (chunk 2) = 3; t.1: is = 1
    assert [o["ty"] for o in model.ops_by_tab["t.0"]] == ["is", "as", "ae"]
    assert [o["ty"] for o in model.ops_by_tab["t.1"]] == ["is"]
    assert model.ops_by_tab["t.0"][0]["s"] == "Hello"


def test_flat_ops_property_concatenates_tabs():
    model = extract_ot_ops(WRAPPED_HTML)
    # 3 (t.0) + 1 (t.1) inner ops; top-level mkch/umv are not content
    assert len(model.ops) == 4
    assert {o["ty"] for o in model.ops} == {"is", "as", "ae"}


def test_revision_and_model_version():
    model = extract_ot_ops(WRAPPED_HTML)
    # revision is the latest chunk's revision; model_version from umv watermark
    assert model.revision == 6
    assert model.model_version == 42


def test_suggestion_colors_merged_across_chunks():
    model = extract_ot_ops(WRAPPED_HTML)
    assert model.suggestion_colors == {"suggest.x": "#ff0000"}


def test_doco_anchor_op_survives_unwrap():
    model = extract_ot_ops(WRAPPED_HTML)
    anchor_op = model.ops_by_tab["t.0"][1]
    assert anchor_op["st"] == "doco_anchor"
    assert anchor_op["sm"]["das_a"]["cv"]["opValue"] == ["kix.c1"]


def test_extract_no_chunk_returns_none():
    assert extract_ot_ops(NO_CHUNK_HTML) is None
