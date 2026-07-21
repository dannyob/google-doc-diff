from unittest.mock import patch

from googleapiclient.errors import HttpError

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
