from unittest.mock import patch

from googleapiclient.errors import HttpError

from google_doc_diff.api import APIError
from google_doc_diff.cli import _is_bulk_tabs_500, cli


class _Resp:
    def __init__(self, status):
        self.status = status
        self.reason = "Internal error"


def _http_error(status):
    return HttpError(_Resp(status), b'{"error": {"message": "Internal error"}}')


def test_recognises_the_bulk_500():
    assert _is_bulk_tabs_500(_http_error(500)) is True
    assert _is_bulk_tabs_500(_http_error(403)) is False
    assert _is_bulk_tabs_500(ValueError("nope")) is False


def _run(runner, tmp_path, args, rich_side_effect, per_tab_doc):
    out = tmp_path / "doc.md"
    with patch("google_doc_diff.cli.parse_doc_id", return_value="DOC123"), \
         patch("google_doc_diff.cli.load_credentials", return_value=object()), \
         patch("google_doc_diff.cli.GdocAPI", return_value=object()), \
         patch("google_doc_diff.cli._pull_rich_document_with_raw",
               side_effect=rich_side_effect) as rich, \
         patch("google_doc_diff.cli.build_per_tab_document",
               return_value=per_tab_doc) as per_tab, \
         patch("google_doc_diff.cli.emit_document_md", return_value="# doc\n"):
        result = runner.invoke(cli, ["pull", "DOC123", "--out", str(out), *args])
    return result, rich, per_tab, out


def test_falls_back_to_per_tab_on_500(cli_runner, temp_dir, minimal_document):
    result, rich, per_tab, out = _run(
        cli_runner, temp_dir, [], _http_error(500), minimal_document
    )
    assert result.exit_code == 0
    assert rich.called and per_tab.called
    assert "degraded" in result.output.lower()
    assert out.read_text() == "# doc\n"


def test_no_sidecar_is_written_on_the_per_tab_path(cli_runner, temp_dir, minimal_document):
    result, _rich, _per_tab, out = _run(
        cli_runner, temp_dir, [], _http_error(500), minimal_document
    )
    assert result.exit_code == 0
    assert not out.with_suffix(".md.pull-state.json").exists()


def test_per_tab_flag_skips_the_bulk_call(cli_runner, temp_dir, minimal_document):
    result, rich, per_tab, _out = _run(
        cli_runner, temp_dir, ["--per-tab"], None, minimal_document
    )
    assert result.exit_code == 0
    assert not rich.called
    assert per_tab.called


def test_no_per_tab_flag_lets_the_500_fail(cli_runner, temp_dir, minimal_document):
    result, _rich, per_tab, _out = _run(
        cli_runner, temp_dir, ["--no-per-tab"], _http_error(500), minimal_document
    )
    assert result.exit_code == 2
    assert not per_tab.called


def test_fallback_warning_mentions_flattened_nested_tabs(cli_runner, temp_dir, minimal_document):
    result, _rich, _per_tab, _out = _run(
        cli_runner, temp_dir, [], _http_error(500), minimal_document
    )
    assert "nested tabs" in result.output.lower()
    assert "flatten" in result.output.lower()


def _run_per_tab_raises(runner, tmp_path, args, rich_side_effect, per_tab_side_effect):
    out = tmp_path / "doc.md"
    with patch("google_doc_diff.cli.parse_doc_id", return_value="DOC123"), \
         patch("google_doc_diff.cli.load_credentials", return_value=object()), \
         patch("google_doc_diff.cli.GdocAPI", return_value=object()), \
         patch("google_doc_diff.cli._pull_rich_document_with_raw",
               side_effect=rich_side_effect) as rich, \
         patch("google_doc_diff.cli.build_per_tab_document",
               side_effect=per_tab_side_effect) as per_tab, \
         patch("google_doc_diff.cli.emit_document_md", return_value="# doc\n"):
        result = runner.invoke(cli, ["pull", "DOC123", "--out", str(out), *args])
    return result, rich, per_tab, out


def test_non_per_tab_error_in_the_fallback_reports_cleanly(cli_runner, temp_dir):
    """The inner fallback (after the bulk 500) used to catch only PerTabError.
    A rate-limited export raises APIError instead, which used to escape as a
    raw traceback with exit 1 -- every other pull failure reports 'api: <msg>'
    and exits 2."""
    result, _rich, _per_tab, _out = _run_per_tab_raises(
        cli_runner, temp_dir, [], _http_error(500),
        APIError("raw HTTP fetch gave up after 5 attempts (last status 429)"),
    )
    assert result.exit_code == 2
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "api:" in result.output


def test_forced_per_tab_500_does_not_reenter_the_fallback(cli_runner, temp_dir):
    """With an explicit --per-tab, a 500 raised from inside the per-tab path
    itself (e.g. get_document_metadata) is not the bulk-tabs 500 and must not
    trigger the auto-fallback, which would misreport 'the full-document fetch
    failed' (never attempted) and re-run the whole per-tab pull a second time."""
    result, rich, per_tab, _out = _run_per_tab_raises(
        cli_runner, temp_dir, ["--per-tab"], None, _http_error(500),
    )
    assert result.exit_code == 2
    assert "api:" in result.output
    assert "full-document fetch failed" not in result.output
    assert per_tab.call_count == 1
    assert not rich.called


def test_stale_sidecar_is_renamed_aside_not_deleted(cli_runner, temp_dir, minimal_document):
    """A sidecar from an earlier rich pull describes a different revision and
    fidelity level than the per-tab markdown it would sit beside; overwriting
    the .md without touching it would leave `gdoc push` reading a stale merge
    base. It must be renamed aside, not silently deleted."""
    old_state = temp_dir / "doc.md.pull-state.json"
    old_state.write_text('{"doc_id": "DOC123"}\n')

    result, _rich, _per_tab, out = _run(
        cli_runner, temp_dir, [], _http_error(500), minimal_document
    )

    assert result.exit_code == 0
    assert not old_state.exists()
    stale = out.with_suffix(".md.pull-state.json.stale")
    assert stale.exists()
    assert stale.read_text() == '{"doc_id": "DOC123"}\n'
    assert "stale" in result.output.lower()
