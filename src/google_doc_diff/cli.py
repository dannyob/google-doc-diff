"""Command-line interface for gdoc."""

from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

import click

from google_doc_diff import __version__
from google_doc_diff.api import GdocAPI, parse_doc_id
from google_doc_diff.ast.from_docs_json import build_document
from google_doc_diff.auth import (
    AuthError,
    auth_status,
    import_gog_token,
    load_credentials,
    run_oauth_flow,
)
from google_doc_diff.emit import emit_document_html, emit_document_md


@click.group()
@click.version_option(version=__version__, prog_name="gdoc")
def cli():
    """Pull Google Docs into high-fidelity Markdown and HTML."""
    pass


# --- auth subgroup --------------------------------------------------------


@cli.group()
def auth():
    """OAuth credential and token management."""


@auth.command("login")
@click.option("--credentials-file", type=click.Path(path_type=Path),
              help="Path to OAuth client credentials.json (default: ~/.config/gdoc-diff/credentials.json)")
@click.option("--import-gog-token", "gog_token", type=click.Path(path_type=Path),
              help="Import a refresh token + client creds from gog auth tokens export <email>.")
def auth_login(credentials_file, gog_token):
    """Authorize gdoc; cache the refresh token."""
    try:
        if gog_token:
            import_gog_token(gog_token_path=gog_token, out_creds_path=credentials_file)
            click.echo("imported gog token to ~/.config/gdoc-diff/")
        else:
            run_oauth_flow(creds_path=credentials_file)
            click.echo("OAuth flow complete; token cached.")
    except AuthError as e:
        click.echo(f"auth: {e}", err=True)
        sys.exit(2)


@auth.command("logout")
def auth_logout():
    """Delete the cached refresh token."""
    info = auth_status()
    p = Path(info["token_path"])
    if p.exists():
        p.unlink()
        click.echo(f"removed {p}")
    else:
        click.echo("no token to remove")


@auth.command("status")
def auth_status_cmd():
    """Print auth diagnostic info."""
    info = auth_status()
    for k, v in info.items():
        click.echo(f"{k}\t{v}")


# --- pull -----------------------------------------------------------------


@cli.command()
@click.argument("doc")
@click.option("--out", type=click.Path(path_type=Path),
              help="Output Markdown path (default: <slug>.md in cwd).")
@click.option("--html-out", type=click.Path(path_type=Path),
              help="Also write rendered HTML to this path.")
@click.option("--extract-assets", is_flag=True,
              help="Download images into <slug>.assets/ and rewrite links.")
@click.option("--revision", help="Pull a specific revision id (Drive v2).")
@click.option("--chip-counts/--no-chip-counts", default=True,
              help="Recover voting/reaction chip counts via an extra markdown export call.")
def pull(doc, out, html_out, extract_assets, revision, chip_counts):
    """Pull a Google Doc and write Markdown (and optionally HTML)."""
    doc_id = parse_doc_id(doc)
    try:
        creds = load_credentials()
    except AuthError as e:
        click.echo(f"auth: {e}", err=True)
        sys.exit(2)

    api = GdocAPI(creds)
    if revision:
        click.echo(f"pulling revision {revision}: not implemented in v1 (use ast/from_google_md)",
                   err=True)
        sys.exit(2)

    try:
        docs_json = api.get_document(doc_id)
        comments_json = api.list_comments(doc_id)
    except Exception as e:
        click.echo(f"api: {e}", err=True)
        sys.exit(2)

    document = build_document(docs_json, comments_json)

    if chip_counts:
        from google_doc_diff.ast.chip_counts import attach_counts_to_chips
        try:
            revs = api.list_revisions(doc_id)
            md_url = ((revs[-1] if revs else {}).get("exportLinks") or {}).get("text/markdown")
            if md_url:
                exported_md = api.fetch_revision_export(md_url).decode("utf-8", errors="replace")
                attach_counts_to_chips(document, exported_md)
        except Exception as e:
            click.echo(f"warning: chip-count recovery failed: {e}", err=True)

    md = emit_document_md(document)

    out_path = out or Path(_slugify(document.title) + ".md")
    out_path.write_text(md)
    click.echo(f"wrote {out_path}")

    if html_out:
        html_path = Path(html_out)
        html_path.write_text(emit_document_html(document))
        click.echo(f"wrote {html_path}")

    image_count = _count_images(document)
    if image_count and not extract_assets:
        click.echo(
            f"warning: {image_count} image URL(s) may rotate. For archival "
            "use, re-run with --extract-assets.",
            err=True,
        )
    elif extract_assets and image_count:
        _extract_assets(document, out_path, api)


# --- revisions -----------------------------------------------------------


@cli.command()
@click.argument("doc")
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
def revisions(doc, fmt):
    """List a doc's revisions (id, modifiedDate, lastModifyingUser)."""
    doc_id = parse_doc_id(doc)
    try:
        creds = load_credentials()
    except AuthError as e:
        click.echo(f"auth: {e}", err=True)
        sys.exit(2)
    api = GdocAPI(creds)
    revs = api.list_revisions(doc_id)
    if fmt == "json":
        click.echo(json.dumps(revs, indent=2))
        return
    click.echo(f"{'id':<12} {'modifiedDate':<26} user")
    click.echo("-" * 80)
    for r in revs:
        user = (r.get("lastModifyingUser") or {}).get("emailAddress", "-")
        click.echo(f"{r['id']:<12} {r.get('modifiedDate', '-'):<26} {user}")


# --- replay --------------------------------------------------------------


@cli.command()
@click.argument("doc")
@click.option("--since", help="ISO timestamp; only events at or after this time.")
@click.option("--until", help="ISO timestamp; only events at or before this time.")
@click.option("--out", type=click.Path(path_type=Path),
              help="Output Markdown path overwritten on each event.")
@click.option("--commit", is_flag=True,
              help="Create one git commit per event in the cwd.")
@click.option("--squash-by-author", default=None,
              help="Coalesce adjacent same-author prose events within DURATION (e.g. 5m, 2h).")
@click.option("--include-comments/--no-include-comments", default=True,
              help="Reconstruct comment state at each event from Drive Comments API.")
@click.option("--dry-run", is_flag=True,
              help="Walk the timeline; print events but don't write or commit.")
@click.option("--resume", is_flag=True,
              help="Continue an interrupted replay (.gdoc-replay-state.json).")
@click.option("--restart", is_flag=True,
              help="Discard any existing replay state and start over.")
def replay(doc, since, until, out, commit, squash_by_author, include_comments,
           dry_run, resume, restart):
    """Walk revisions + comment events and emit one .md (and optional commit) per event."""
    from datetime import datetime as _dt

    from google_doc_diff.replay import git as gitwrap
    from google_doc_diff.replay.runner import ReplayRunner, RunnerOptions
    from google_doc_diff.replay.state import (
        EventState,
        ReplayState,
        read_state,
        remove_state,
        write_state,
    )
    from google_doc_diff.replay.timeline import (
        build_timeline,
        event_to_state_dict,
        timeline_hash,
    )

    doc_id = parse_doc_id(doc)
    cwd = Path.cwd()

    existing = read_state(cwd)
    if existing and not (resume or restart):
        click.echo(
            f"{cwd}/.gdoc-replay-state.json exists. Use --resume to continue "
            "or --restart to discard.",
            err=True,
        )
        sys.exit(2)
    if restart:
        remove_state(cwd)
        existing = None

    try:
        creds = load_credentials()
    except AuthError as e:
        click.echo(f"auth: {e}", err=True)
        sys.exit(2)
    api = GdocAPI(creds)

    revisions = api.list_revisions(doc_id)
    comments = api.list_comments(doc_id) if include_comments else []

    since_dt = _dt.fromisoformat(since) if since else None
    until_dt = _dt.fromisoformat(until) if until else None
    squash = _parse_duration(squash_by_author) if squash_by_author else None

    events = build_timeline(
        revisions, comments,
        since=since_dt, until=until_dt, squash_by_author=squash,
    )
    new_hash = timeline_hash(events)

    if existing and resume:
        if existing.timeline_hash != new_hash:
            click.echo(
                "Timeline hash mismatch (revisions or comments changed since "
                "the interrupted run). Pass --restart to discard, or revert "
                "the upstream changes.",
                err=True,
            )
            sys.exit(2)

    out_path = out or Path(_slugify(doc_id) + ".md")

    if commit:
        # The state file itself is always changing during replay; same with
        # the output .md (we're about to overwrite it on each event). Both
        # are expected-dirty.
        ignore = [".gdoc-replay-state.json"]
        try:
            ignore.append(str(out_path.relative_to(cwd)))
        except ValueError:
            pass
        if not gitwrap.is_clean(cwd, ignore=ignore):
            click.echo("git working tree is dirty; commit or stash first.", err=True)
            sys.exit(2)

    if dry_run:
        for ev in events:
            click.echo(
                f"{ev.timestamp.isoformat()}  {ev.kind:<14} "
                f"{(ev.revision_id or ev.comment_id or '-'):<26} {ev.author}"
            )
        return

    # Build / reuse state file.
    if existing and resume:
        state = existing
    else:
        state = ReplayState(
            doc_id=doc_id, out_path=str(out_path),
            extract_assets=False, include_comments=include_comments,
            since=since, until=until,
            timeline_hash=new_hash,
            events=[EventState(**event_to_state_dict(e)) for e in events],
        )
        write_state(state, cwd)

    runner = ReplayRunner(api, doc_id, RunnerOptions(
        out_path=out_path, commit=commit, cwd=cwd,
        include_comments=include_comments,
    ))

    # Resume: skip events already committed.
    pending: list = []
    pending_states: list[EventState] = []
    for ev, est in zip(events, state.events, strict=True):
        if est.status == "committed":
            continue
        pending.append(ev)
        pending_states.append(est)

    def _on_event(ev, sha):
        for est in state.events:
            if est.id == ev.event_id and est.status != "committed":
                est.status = "committed"
                est.git_sha = sha
                break
        write_state(state, cwd)
        click.echo(f"  {ev.kind:<14} {ev.timestamp.isoformat()}  {sha or '(no commit)'}")

    runner.execute(pending, on_event=_on_event)
    click.echo(
        f"replayed {len(pending)} event(s); state: {cwd}/.gdoc-replay-state.json"
    )
    if commit and pending:
        click.echo(
            f"head state (live doc, suggestions, full chip metadata) written "
            f"uncommitted to {out_path}; `git diff HEAD` to see what's changed "
            f"since the last replayed event."
        )


def _parse_duration(s: str):
    """Accept Go-style duration: 5m, 300s, 1h, 2h30m. Returns timedelta."""
    from datetime import timedelta as _td
    pattern = re.compile(r"(\d+)([smh])")
    matches = pattern.findall(s)
    if not matches:
        raise click.BadParameter(f"unrecognized duration: {s!r}")
    total = 0.0
    for n, unit in matches:
        n = int(n)
        if unit == "s":
            total += n
        elif unit == "m":
            total += n * 60
        elif unit == "h":
            total += n * 3600
    return _td(seconds=total)


# --- diff ----------------------------------------------------------------


@cli.command()
@click.argument("doc")
@click.argument("path", required=False, type=click.Path(path_type=Path))
@click.option("--color", type=click.Choice(["auto", "always", "never"]), default="auto")
def diff(doc, path, color):
    """Pull current Doc; show unified diff against PATH (default: <slug>.md)."""
    doc_id = parse_doc_id(doc)
    try:
        creds = load_credentials()
    except AuthError as e:
        click.echo(f"auth: {e}", err=True)
        sys.exit(2)
    api = GdocAPI(creds)
    try:
        docs_json = api.get_document(doc_id)
        comments_json = api.list_comments(doc_id)
    except Exception as e:
        click.echo(f"api: {e}", err=True)
        sys.exit(2)

    document = build_document(docs_json, comments_json)
    md_new = emit_document_md(document)

    target = path or Path(_slugify(document.title) + ".md")
    if not target.exists():
        click.echo(f"local file not found: {target}", err=True)
        sys.exit(2)
    md_old = target.read_text()

    if md_new == md_old:
        sys.exit(0)

    use_color = color == "always" or (color == "auto" and sys.stdout.isatty())
    diff_lines = difflib.unified_diff(
        md_old.splitlines(keepends=True),
        md_new.splitlines(keepends=True),
        fromfile=f"local:{target}",
        tofile=f"remote:{doc_id}",
    )
    for line in diff_lines:
        if use_color:
            click.echo(_colorize(line), nl=False)
        else:
            click.echo(line, nl=False)
    sys.exit(1)


# --- helpers --------------------------------------------------------------


def _slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "untitled"


def _count_images(document) -> int:
    from google_doc_diff.ast.nodes import Image

    n = 0

    def walk(node):
        nonlocal n
        if isinstance(node, Image):
            n += 1
        for attr in ("runs", "blocks", "rows", "cells", "children", "tabs"):
            children = getattr(node, attr, None)
            if children:
                for c in children:
                    walk(c)

    for tab in document.tabs:
        walk(tab)
    return n


def _extract_assets(document, md_path: Path, api: GdocAPI) -> None:
    """Download every Image src into <md-stem>.assets/ and rewrite the AST.

    NB: This rewrites the in-memory AST and the on-disk .md after the fact.
    For now we just announce the count; full extraction is left to a follow-up
    once we have a real test doc with images.
    """
    from google_doc_diff.ast.nodes import Image

    assets_dir = md_path.with_suffix(".assets")
    assets_dir.mkdir(parents=True, exist_ok=True)
    saved = 0

    def walk(node):
        nonlocal saved
        if isinstance(node, Image) and node.src.startswith("http"):
            try:
                blob = api.fetch_revision_export(node.src)
                fname = f"{node.image_id}{_guess_ext(node.src)}"
                (assets_dir / fname).write_bytes(blob)
                node.src = f"{assets_dir.name}/{fname}"
                saved += 1
            except Exception as e:
                click.echo(f"image {node.image_id}: {e}", err=True)
        for attr in ("runs", "blocks", "rows", "cells", "children", "tabs"):
            children = getattr(node, attr, None)
            if children:
                for c in children:
                    walk(c)

    for tab in document.tabs:
        walk(tab)
    if saved:
        # Re-emit with updated image src values.
        md_path.write_text(emit_document_md(document))
        click.echo(f"extracted {saved} image(s) to {assets_dir}/")


def _guess_ext(url: str) -> str:
    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
        if ext in url.lower():
            return ext
    return ".bin"


def _colorize(line: str) -> str:
    if line.startswith("+++") or line.startswith("---"):
        return f"\033[1m{line}\033[0m"
    if line.startswith("+"):
        return f"\033[32m{line}\033[0m"
    if line.startswith("-"):
        return f"\033[31m{line}\033[0m"
    if line.startswith("@@"):
        return f"\033[36m{line}\033[0m"
    return line


if __name__ == "__main__":
    cli()
