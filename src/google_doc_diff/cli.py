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
from google_doc_diff.assets import count_images, extract_image_assets, has_pua_widgets
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
        document = _pull_rich_document(api, doc_id, chip_counts=chip_counts)
    except Exception as e:
        click.echo(f"api: {e}", err=True)
        sys.exit(2)

    md = emit_document_md(document)

    out_path = out or Path(_slugify(document.title) + ".md")
    out_path.write_text(md)
    click.echo(f"wrote {out_path}")

    if html_out:
        html_path = Path(html_out)
        html_path.write_text(emit_document_html(document))
        click.echo(f"wrote {html_path}")

    image_count = count_images(document)
    if image_count and not extract_assets:
        click.echo(
            f"warning: {image_count} image URL(s) may rotate. For archival "
            "use, re-run with --extract-assets.",
            err=True,
        )
    elif extract_assets and image_count:
        saved = extract_image_assets(
            document, out_path, api,
            on_error=lambda node, e: click.echo(f"image {node.image_id}: {e}", err=True),
        )
        if saved:
            click.echo(f"extracted {saved} image(s) to {out_path.with_suffix('.assets')}/")


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
@click.option("--commit/--no-commit", default=True,
              help="Create one git commit per event in the cwd (default). "
                   "Pass --no-commit to write the file without touching git.")
@click.option("--squash-by-author", default=None,
              help="Coalesce adjacent same-author prose events within DURATION (e.g. 5m, 2h).")
@click.option("--include-comments/--no-include-comments", default=True,
              help="Reconstruct comment state at each event from Drive Comments API.")
@click.option("--extract-assets", is_flag=True,
              help="Download images for the live (working-tree) state into <slug>.assets/.")
@click.option("--dry-run", is_flag=True,
              help="Walk the timeline; print events but don't write or commit.")
@click.option("--resume", is_flag=True,
              help="Continue an interrupted replay (.gdoc-replay-state.json).")
@click.option("--restart", is_flag=True,
              help="Discard any existing replay state and start over.")
def replay(doc, since, until, out, commit, squash_by_author, include_comments,
           extract_assets, dry_run, resume, restart):
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
        ok, reason = _can_reconcile(existing, events)
        if not ok:
            click.echo(
                f"Cannot resume: {reason}. Pass --restart to discard the saved "
                "state and start over.",
                err=True,
            )
            sys.exit(2)

    out_path = out or Path(_slugify(doc_id) + ".md")

    if commit:
        if not (cwd / ".git").exists():
            click.echo(
                f"{cwd} is not a git repository. Run `git init` first, or "
                "pass --no-commit to write the file without committing.",
                err=True,
            )
            sys.exit(2)
        # State file + the in-flight output .md are expected-dirty.
        ignore = [".gdoc-replay-state.json"]
        try:
            ignore.append(str(out_path.relative_to(cwd)))
        except ValueError:
            pass
        if not gitwrap.is_clean(cwd, ignore=ignore):
            click.echo("git working tree is dirty; commit or stash first, or "
                       "pass --no-commit.", err=True)
            sys.exit(2)

    if dry_run:
        for ev in events:
            click.echo(
                f"{ev.timestamp.isoformat()}  {ev.kind:<14} "
                f"{(ev.revision_id or ev.comment_id or '-'):<26} {ev.author}"
            )
        return

    # Build / reuse state file. On resume, carry per-event status from
    # the saved state forward into the (possibly-extended) new timeline.
    if existing and resume:
        prior = {e.id: e for e in existing.events}
        merged_events: list[EventState] = []
        for ev in events:
            d = event_to_state_dict(ev)
            saved = prior.get(ev.event_id)
            if saved:
                d["status"] = saved.status
                d["git_sha"] = saved.git_sha
            merged_events.append(EventState(**d))
        state = ReplayState(
            doc_id=existing.doc_id,
            out_path=existing.out_path,
            extract_assets=existing.extract_assets,
            include_comments=existing.include_comments,
            since=existing.since, until=existing.until,
            timeline_hash=new_hash,
            events=merged_events,
        )
        new_count = sum(1 for e in events if e.event_id not in prior)
        if new_count:
            click.echo(
                f"resuming: {new_count} new event(s) appeared upstream since "
                "the last run."
            )
        write_state(state, cwd)
    else:
        state = ReplayState(
            doc_id=doc_id, out_path=str(out_path),
            extract_assets=extract_assets, include_comments=include_comments,
            since=since, until=until,
            timeline_hash=new_hash,
            events=[EventState(**event_to_state_dict(e)) for e in events],
        )
        write_state(state, cwd)

    runner = ReplayRunner(api, doc_id, RunnerOptions(
        out_path=out_path, commit=commit, cwd=cwd,
        include_comments=include_comments,
        extract_assets=extract_assets,
    ))

    # Resume: skip events already committed.
    pending: list = []
    pending_states: list[EventState] = []
    for ev, est in zip(events, state.events, strict=True):
        # 'committed' = done; everything else (pending, failed) gets retried.
        if est.status == "committed":
            continue
        pending.append(ev)
        pending_states.append(est)

    def _on_event(ev, sha):
        # ev=None signals a non-event message from the runner (e.g.
        # "rich head state failed"); just print and bail.
        if ev is None:
            click.echo(f"  {sha}", err=True)
            return
        skipped = isinstance(sha, str) and sha.startswith("(skipped")
        new_status = "failed" if skipped else "committed"
        for est in state.events:
            if est.id == ev.event_id and est.status != "committed":
                est.status = new_status
                est.git_sha = None if skipped else sha
                break
        write_state(state, cwd)
        marker = sha if isinstance(sha, str) else (sha or "(no commit)")
        click.echo(f"  {ev.kind:<14} {ev.timestamp.isoformat()}  {marker}")

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


# --- fetch (refresh working-tree state) ----------------------------------


@cli.command()
@click.argument("doc", required=False)
@click.option("--out", type=click.Path(path_type=Path),
              help="Output path (default: read from .gdoc-replay-state.json or "
                   "fall back to <slug>.md).")
@click.option("--extract-assets", is_flag=True)
def fetch(doc, out, extract_assets):
    """Refresh the working-tree state with a fresh live pull.

    Designed for use in directories where `gdoc replay --commit` has built a
    git history: re-runs only the rich JSON-derived live-state pass, so
    `git diff HEAD` gives you 'what's changed in the doc since the last
    replayed event' — without re-walking the timeline.

    With no DOC argument, reads the doc id and out path from
    .gdoc-replay-state.json in the current directory.
    """
    from google_doc_diff.replay.state import read_state

    cwd = Path.cwd()
    state = read_state(cwd)
    if doc:
        doc_id = parse_doc_id(doc)
    elif state:
        doc_id = state.doc_id
    else:
        click.echo("no DOC argument given and no .gdoc-replay-state.json in cwd; "
                   "pass a doc id or url explicitly.", err=True)
        sys.exit(2)

    if not out and state and not doc:
        out_path = Path(state.out_path)
    else:
        out_path = out or Path(_slugify(doc_id) + ".md")

    try:
        creds = load_credentials()
    except AuthError as e:
        click.echo(f"auth: {e}", err=True)
        sys.exit(2)
    api = GdocAPI(creds)

    try:
        document = _pull_rich_document(api, doc_id, chip_counts=True)
    except Exception as e:
        click.echo(f"api: {e}", err=True)
        sys.exit(2)
    out_path.write_text(emit_document_md(document))
    click.echo(f"refreshed {out_path}")
    if (cwd / ".git").exists():
        click.echo("`git diff HEAD` to see what's changed since the last commit.")


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
        document = _pull_rich_document(api, doc_id, chip_counts=True)
    except Exception as e:
        click.echo(f"api: {e}", err=True)
        sys.exit(2)
    md_new = emit_document_md(document)

    target = path or Path(_slugify(document.title) + ".md")
    if not target.exists():
        click.echo(f"local file not found: {target}", err=True)
        sys.exit(2)
    md_old = target.read_text()

    # Strip wall-clock metadata from both sides so an identical pull doesn't
    # show as a diff. captured_at and last_modifying_user can differ between
    # pulls without any actual content change; revision_id changes on every
    # save (including no-op saves), so it's also dropped from the comparison.
    md_old_norm = _strip_volatile_frontmatter(md_old)
    md_new_norm = _strip_volatile_frontmatter(md_new)
    if md_new_norm == md_old_norm:
        sys.exit(0)

    use_color = color == "always" or (color == "auto" and sys.stdout.isatty())
    diff_lines = difflib.unified_diff(
        md_old_norm.splitlines(keepends=True),
        md_new_norm.splitlines(keepends=True),
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


def _can_reconcile(existing, new_events):
    """Decide whether a saved replay state can be reused against a freshly-
    computed timeline. Returns (ok, reason).

    Rule: any committed event that STILL exists in the new timeline must
    have unchanged kind/timestamp/author. Anything else is fine:
      - new events appended upstream → fold them in as pending
      - committed events that vanished upstream → keep the local commit and
        drop the state entry (Drive's revision compaction does this routinely;
        comments can be deleted; not a conflict)
    """
    new_by_id = {ev.event_id: ev for ev in new_events}
    for est in existing.events:
        if est.status != "committed":
            continue
        cur = new_by_id.get(est.id)
        if cur is None:
            continue   # gone from upstream; local commit is the record
        if cur.kind != est.kind:
            return False, f"event {est.id} changed kind ({est.kind} → {cur.kind})"
        if cur.timestamp.isoformat() != est.timestamp:
            return False, f"event {est.id} changed timestamp"
        if cur.author != est.author:
            return False, f"event {est.id} changed author ({est.author} → {cur.author})"
    return True, ""


def _pull_rich_document(api, doc_id, *, chip_counts=True):
    """Shared rich-pull path used by gdoc pull / gdoc diff / gdoc fetch.

    Fetches Docs JSON + Drive Comments, builds the AST, and (if chip_counts
    and the AST contains PUA widget placeholders) cross-references with the
    markdown export to recover chip emoji + counts.
    """
    docs_json = api.get_document(doc_id)
    comments_json = api.list_comments(doc_id)
    document = build_document(docs_json, comments_json)
    if chip_counts and has_pua_widgets(document):
        from google_doc_diff.ast.chip_counts import attach_widget_renderings
        try:
            revs = api.list_revisions(doc_id)
            md_url = ((revs[-1] if revs else {}).get("exportLinks") or {}).get("text/markdown")
            if md_url:
                md_text = api.fetch_revision_export(md_url).decode("utf-8", errors="replace")
                attach_widget_renderings(document, md_text)
        except Exception:
            pass   # best-effort
    return document


_VOLATILE_FRONTMATTER_KEYS = ("captured_at", "last_modifying_user", "revision_id")


def _strip_volatile_frontmatter(md: str) -> str:
    """Remove frontmatter lines whose values legitimately differ between
    pulls of an unchanged doc (wall-clock timestamps and per-save metadata).
    Anything else in the frontmatter — title, source_mode,
    comments_preserved, etc. — is kept so real metadata changes still show.
    """
    if not md.startswith("---\n"):
        return md
    end = md.find("\n---\n", 4)
    if end < 0:
        return md
    head = md[4:end]
    body = md[end + 5:]
    kept = []
    for line in head.split("\n"):
        if any(line.startswith(k + ":") for k in _VOLATILE_FRONTMATTER_KEYS):
            continue
        kept.append(line)
    return "---\n" + "\n".join(kept) + "\n---\n" + body


def _slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "untitled"


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
