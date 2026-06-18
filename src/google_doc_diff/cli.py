"""Command-line interface for gdoc."""

from __future__ import annotations

import difflib
import json
import logging
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

logger = logging.getLogger(__name__)


def resolve_doc_target(s: str) -> tuple[str, Path | None]:
    """Resolve a positional DOC argument to (doc_id, path_hint).

    Accepts any of:
      - A bare Drive doc ID
      - A Google Docs / Drive URL
      - A path to an existing local .md file (pulled or replayed earlier);
        the file's YAML frontmatter must carry `doc_id: ...`.

    Returns (doc_id, path_hint). `path_hint` is the local file path when the
    argument was a file (so commands can default --out to the same path);
    None otherwise.
    """
    p = Path(s)
    if p.is_file() and p.suffix in (".md", ".markdown"):
        doc_id = _read_doc_id_from_frontmatter(p)
        if doc_id:
            return doc_id, p
        raise click.ClickException(
            f"{p} is a markdown file but has no `doc_id:` in its YAML frontmatter; "
            "pass a doc ID or URL instead."
        )
    return parse_doc_id(s), None


def _read_doc_id_from_frontmatter(p: Path) -> str | None:
    """Pull `doc_id:` out of a YAML frontmatter block at the top of a .md file."""
    text = p.read_text()
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    for line in text[4:end].splitlines():
        if line.startswith("doc_id:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return None


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
@click.option("--kix-cookies", type=click.Path(path_type=Path),
              help="Path to a Chromium Cookies SQLite file for kix enrichment.")
@click.option("--kix-profile",
              help="Chrome profile name for kix enrichment (e.g. 'Profile 1').")
@click.option("--no-kix", is_flag=True,
              help="Skip kix enrichment even if Chrome cookies are available.")
@click.option("--verbose", is_flag=True, help="Print enrichment diagnostics.")
@click.option("--readable", is_flag=True,
              help="Strip paragraph_id anchors for a human-readable view. "
                   "Disables surgical 3-way merge on a later `gdoc push`.")
def pull(doc, out, html_out, extract_assets, revision, chip_counts, kix_cookies, kix_profile, no_kix, verbose, readable):
    """Pull a Google Doc and write Markdown (and optionally HTML).

    DOC is a doc ID, a Google Docs URL, or the path to an existing local
    .md file (whose frontmatter doc_id is used; --out defaults to that
    same path so re-pulls overwrite in place)."""
    doc_id, path_hint = resolve_doc_target(doc)
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
        document, docs_json = _pull_rich_document_with_raw(api, doc_id, chip_counts=chip_counts)
    except Exception as e:
        click.echo(f"api: {e}", err=True)
        sys.exit(2)

    if not no_kix:
        _try_kix_enrichment(
            document, doc_id,
            kix_cookies=str(kix_cookies) if kix_cookies else None,
            kix_profile=kix_profile,
            verbose=verbose,
        )

    md = emit_document_md(document, readable=readable)

    out_path = out or path_hint or Path(_slugify(document.title) + ".md")
    out_path.write_text(md)
    click.echo(f"wrote {out_path}")

    # Write the merge-base sidecar so `gdoc push` (default = 3-way merge)
    # has the doc's pull-time state to diff against.
    state_path = out_path.with_suffix(out_path.suffix + ".pull-state.json")
    state_path.write_text(json.dumps({
        "doc_id": doc_id,
        "revision_id": document.revision_id,
        "docs_json": docs_json,
        "base_md": md,
    }, default=str) + "\n")

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
    """List a doc's revisions (id, modifiedDate, lastModifyingUser).

    DOC is a doc ID, a URL, or a local .md file."""
    doc_id, _ = resolve_doc_target(doc)
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
              help="Continue an interrupted replay (.gdoc-state/<doc_id>.json).")
@click.option("--restart", is_flag=True,
              help="Discard any existing replay state and start over.")
@click.option("--state", "state_override", type=click.Path(path_type=Path),
              help="Explicit replay-state file path "
                   "(default: .gdoc-state/<doc_id>.json).")
def replay(doc, since, until, out, commit, squash_by_author, include_comments,
           extract_assets, dry_run, resume, restart, state_override):
    """Walk revisions + comment events and emit one .md (and optional commit) per event.

    DOC is a doc ID, a URL, or a local .md file (whose frontmatter doc_id
    is used; --out defaults to that same path so commits target it)."""
    from datetime import datetime as _dt

    from google_doc_diff.replay import git as gitwrap
    from google_doc_diff.replay.runner import ReplayRunner, RunnerOptions
    from google_doc_diff.replay.state import (
        EventState,
        ReplayState,
        default_state_path,
        legacy_state_path,
        read_state,
        remove_state,
        write_state,
    )
    from google_doc_diff.replay.timeline import (
        build_timeline,
        event_to_state_dict,
        timeline_hash,
    )

    doc_id, path_hint = resolve_doc_target(doc)
    cwd = Path.cwd()
    out_path = out or path_hint or Path(_slugify(doc_id) + ".md")
    state_file = state_override or default_state_path(doc_id, cwd)

    existing = read_state(state_file)
    if existing is None and not state_override:
        legacy_path = legacy_state_path(cwd)
        legacy = read_state(legacy_path)
        if legacy is not None and legacy.doc_id == doc_id:
            write_state(legacy, state_file)   # migrate into .gdoc-state/<doc_id>.json
            existing = legacy
            click.echo(f"migrated legacy {legacy_path} -> {state_file}.", err=True)
    if restart:
        remove_state(state_file)
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

    # If no state file but we're committing into an existing git history
    # (e.g. a fresh checkout), rebuild the committed-set from git so resume
    # continues instead of duplicating commits.
    if existing is None and commit and not restart and (cwd / ".git").exists():
        from google_doc_diff.replay.state import reconstruct_committed_set
        recovered = reconstruct_committed_set(events, cwd, out_path)
        if recovered:
            existing = ReplayState(
                doc_id=doc_id, out_path=str(out_path),
                extract_assets=extract_assets, include_comments=include_comments,
                since=since, until=until, timeline_hash=new_hash,
                events=[EventState(**event_to_state_dict(e)) for e in events],
            )
            for est in existing.events:
                if est.id in recovered:
                    est.status = "committed"
                    est.git_sha = recovered[est.id]
            click.echo(
                f"reconstructed {len(recovered)} committed event(s) from git history."
            )

    if existing and not (resume or restart):
        click.echo(
            f"replay history exists for this doc ({state_file} or git). "
            "Use --resume to continue or --restart to discard.",
            err=True,
        )
        sys.exit(2)

    if existing and resume:
        ok, reason = _can_reconcile(existing, events)
        if not ok:
            click.echo(
                f"Cannot resume: {reason}. Pass --restart to discard the saved "
                "state and start over.",
                err=True,
            )
            sys.exit(2)

    if commit:
        if not (cwd / ".git").exists():
            click.echo(
                f"{cwd} is not a git repository. Run `git init` first, or "
                "pass --no-commit to write the file without committing.",
                err=True,
            )
            sys.exit(2)
        # State dir + the in-flight output .md are expected-dirty.
        ignore = []
        try:
            ignore.append(str(out_path.relative_to(cwd)))
        except ValueError:
            # out_path is already relative; use it as-is.
            if not out_path.is_absolute():
                ignore.append(str(out_path))
        if not gitwrap.is_clean(cwd, ignore=ignore,
                                ignore_prefixes=[".gdoc-state/", ".gdoc-state"]):
            click.echo("git working tree is dirty; commit or stash first, or "
                       "pass --no-commit.", err=True)
            sys.exit(2)
        # Nudge the user to gitignore the cache dir (we don't edit it for them).
        gi = cwd / ".gitignore"
        already = gi.exists() and any(
            line.strip().rstrip("/") == ".gdoc-state"
            for line in gi.read_text().splitlines()
        )
        if not already:
            click.echo("hint: add '.gdoc-state/' to .gitignore "
                       "(replay state is a rebuildable cache).", err=True)

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
        write_state(state, state_file)
    else:
        state = ReplayState(
            doc_id=doc_id, out_path=str(out_path),
            extract_assets=extract_assets, include_comments=include_comments,
            since=since, until=until,
            timeline_hash=new_hash,
            events=[EventState(**event_to_state_dict(e)) for e in events],
        )
        write_state(state, state_file)

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
        write_state(state, state_file)
        marker = sha if isinstance(sha, str) else (sha or "(no commit)")
        click.echo(f"  {ev.kind:<14} {ev.timestamp.isoformat()}  {marker}")

    runner.execute(pending, on_event=_on_event)
    click.echo(
        f"replayed {len(pending)} event(s); state: {state_file}"
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
              help="Output path (default: read from .gdoc-state/<doc_id>.json or "
                   "fall back to <slug>.md).")
@click.option("--extract-assets", is_flag=True)
def fetch(doc, out, extract_assets):
    """Refresh the working-tree state with a fresh live pull.

    Designed for use in directories where `gdoc replay` has built a git
    history: re-runs only the rich JSON-derived live-state pass, so
    `git diff HEAD` gives you 'what's changed in the doc since the last
    replayed event' — without re-walking the timeline.

    DOC can be a doc ID, a URL, or a local .md file (in which case the
    doc id is read from its frontmatter and the file is refreshed in
    place). Requires a DOC argument (a doc id, URL, or .md file whose
    frontmatter doc id is used); per-doc state has no single cwd default.
    """
    from google_doc_diff.replay.state import default_state_path, read_state

    cwd = Path.cwd()
    if not doc:
        click.echo("fetch needs a doc id, URL, or .md path "
                   "(per-doc state means there is no single default).", err=True)
        sys.exit(2)
    doc_id, path_hint = resolve_doc_target(doc)
    state = read_state(default_state_path(doc_id, cwd))

    if out:
        out_path = out
    elif path_hint:
        out_path = path_hint
    elif state:
        out_path = Path(state.out_path)
    else:
        out_path = Path(_slugify(doc_id) + ".md")

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
    """Pull current Doc; show unified diff against PATH.

    DOC can be a doc ID, a URL, or a local .md file. When DOC is a .md
    file and PATH is omitted, the diff is run against that same file."""
    doc_id, path_hint = resolve_doc_target(doc)
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

    target = path or path_hint or Path(_slugify(document.title) + ".md")
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
    have an unchanged timestamp. Tolerated drift:
      - new events appended upstream → folded in as pending
      - committed events vanished upstream → kept as local commit, dropped
        from the state file (Drive's revision compaction is routine)
      - author changed for a committed event → silently kept (Drive returns
        inconsistent lastModifyingUser values across calls)
      - kind drift can't happen in practice: event_id encodes the kind for
        replies, and a revision id always corresponds to a prose_change
    """
    new_by_id = {ev.event_id: ev for ev in new_events}
    for est in existing.events:
        if est.status != "committed":
            continue
        cur = new_by_id.get(est.id)
        if cur is None:
            continue   # gone from upstream; local commit is the record
        if cur.timestamp.isoformat() != est.timestamp:
            return False, f"event {est.id} changed timestamp"
    return True, ""


def _pull_rich_document(api, doc_id, *, chip_counts=True):
    """Shared rich-pull path used by gdoc pull / gdoc diff / gdoc fetch.

    Fetches Docs JSON + Drive Comments, builds the AST, and (if chip_counts
    and the AST contains PUA widget placeholders) cross-references with the
    markdown export to recover chip emoji + counts.
    """
    document, _docs_json = _pull_rich_document_with_raw(api, doc_id, chip_counts=chip_counts)
    return document


def _pull_rich_document_with_raw(api, doc_id, *, chip_counts=True):
    """Like _pull_rich_document but also returns the raw docs_json.

    `gdoc push` uses the raw JSON to write a `.pull-state.json` sidecar so a
    subsequent push has the AST snapshot at pull-time available as the
    three-way merge base.
    """
    docs_json = api.get_document(doc_id)
    comments_json = api.list_comments(doc_id)
    document = build_document(docs_json, comments_json)
    if chip_counts and has_pua_widgets(document):
        _attach_chip_counts(api, doc_id, document=document)
    return document, docs_json


def _attach_chip_counts(api, doc_id, *, document) -> None:
    """Best-effort: recover chip emoji + counts from the markdown export.

    Failures (the export endpoint can be slow or flaky on large docs) leave
    the '?' placeholders in place but are logged so they're not invisible."""
    from google_doc_diff.ast.chip_counts import attach_widget_renderings
    try:
        revs = api.list_revisions(doc_id)
        md_url = ((revs[-1] if revs else {}).get("exportLinks") or {}).get("text/markdown")
        if md_url:
            md_text = api.fetch_revision_export(md_url).decode("utf-8", errors="replace")
            attach_widget_renderings(document, md_text)
    except Exception as exc:
        logger.warning("chip-count recovery via markdown export failed: %s", exc)


def _have_browser_cookie3() -> bool:
    """True if the optional browser-cookie3 dependency is importable."""
    try:
        import browser_cookie3  # noqa: F401
    except ImportError:
        return False
    return True


_KIX_VERBOSE_HANDLER = "gdoc-kix-verbose"


def _enable_kix_verbose_logging() -> None:
    """Surface the kix layer's own DEBUG diagnostics (e.g. the exact HTTP
    status of a failed /edit fetch) to stderr, scoped so urllib3 etc. stay quiet."""
    logger = logging.getLogger("google_doc_diff.kix")
    if any(getattr(h, "name", None) == _KIX_VERBOSE_HANDLER for h in logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.name = _KIX_VERBOSE_HANDLER
    handler.setFormatter(logging.Formatter("kix: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False


def _try_kix_enrichment(doc, doc_id, *, kix_cookies=None, kix_profile=None, verbose=False):
    """Attempt kix enrichment; return EnrichResult or None."""
    from google_doc_diff.kix import enrich_from_kix, extract_ot_ops, load_kix_session
    from google_doc_diff.kix.auth import resolve_cookie_path

    if verbose:
        _enable_kix_verbose_logging()

    if not _have_browser_cookie3():
        if verbose:
            click.echo(
                "kix enrichment: skipped (browser-cookie3 not installed; "
                "install the optional extra with `pip install 'google-doc-diff[kix]'`)",
                err=True,
            )
        return None

    if resolve_cookie_path(cookie_path=kix_cookies, profile_name=kix_profile) is None:
        if verbose:
            click.echo("kix enrichment: skipped (no Chrome cookies found)", err=True)
        return None

    session = load_kix_session(doc_id, cookie_path=kix_cookies, profile_name=kix_profile)
    if session is None:
        if verbose:
            click.echo(
                "kix enrichment: skipped (Chrome cookies found, but the doc's /edit "
                "page was not authorized — likely the wrong Google account is signed "
                "in, or that account has no access to this doc)",
                err=True,
            )
        return None

    model = extract_ot_ops(session.edit_html)
    if model is None:
        if verbose:
            click.echo("kix enrichment: skipped (could not extract OT model)", err=True)
        return None

    result = enrich_from_kix(doc, model)
    if verbose:
        parts = []
        if result.suggestion_colors_applied:
            parts.append(f"suggestion colors: {result.suggestion_colors_applied}")
        if result.voting_chips_enriched:
            parts.append(f"voting chips: {result.voting_chips_enriched}")
        summary = ", ".join(parts) if parts else "no enrichments applied"
        click.echo(f"kix enrichment: applied ({summary})", err=True)
    return result


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


@cli.command()
@click.argument("path", type=click.Path(path_type=Path, exists=True))
@click.argument("doc", required=False)
@click.option("--new", "new_doc", is_flag=True,
              help="Create a fresh Google Doc and push to it (instead of DOC).")
@click.option("--title", default=None,
              help="Title for --new. Defaults to the markdown's frontmatter title.")
@click.option("--force", "force", is_flag=True,
              help="Skip 3-way merge; overwrite remote with local. The default "
                   "push path is a fetch-and-merge that writes conflict markers "
                   "into the local md when both sides edited the same block.")
@click.option("--continue", "continue_", is_flag=True,
              help="Resume a push after manually resolving conflict markers. "
                   "Refuses to apply if any `.gd-conflict` divs remain in PATH.")
@click.option("--abort", "abort", is_flag=True,
              help="Discard conflict markers and overwrite PATH from the remote. "
                   "Refuses if PATH has no `.gd-conflict` divs.")
@click.option("--dry-run", "dry_run", is_flag=True,
              help="Compute the OpPlan but don't write anything. Exit 0.")
@click.option("--plan-only", "plan_only", type=click.Path(path_type=Path), default=None,
              help="Write the OpPlan as JSON to PATH; don't apply.")
def push(path, doc, new_doc, title, force, continue_, abort, dry_run, plan_only):
    """Push a local .md back to a Google Doc.

    \b
    Modes:
      gdoc push PATH.md DOC                   fetch + 3-way merge + apply
      gdoc push PATH.md DOC --force           overwrite remote (skip merge)
      gdoc push PATH.md DOC --continue        resume after resolving markers
      gdoc push PATH.md DOC --abort           discard markers, restore from remote
      gdoc push PATH.md --new --title TITLE   create a new doc and push
      gdoc push PATH.md DOC --dry-run         compute plan only
      gdoc push PATH.md DOC --plan-only OUT   write plan JSON to OUT

    The default path reads `<PATH>.pull-state.json` (written by
    `gdoc pull`) as the merge base. On conflict it rewrites PATH with
    `.gd-conflict` divs and exits non-zero so you can resolve and re-push
    via `--continue` — or `--abort` to throw away your edits and restart
    from the remote. Every successful push refreshes the sidecar so
    subsequent merges have a fresh base.
    """
    from google_doc_diff.cli_push import (
        NoConflictToAbortError,
        UnresolvedConflictError,
        format_plan_summary,
        push_abort,
        push_continue,
        push_dry_run,
        push_force,
        push_merge,
        push_new,
        write_plan_json,
    )

    exclusive = [name for flag, name in (
        (force, "--force"), (continue_, "--continue"), (abort, "--abort"),
    ) if flag]
    if len(exclusive) > 1:
        raise click.ClickException(
            f"{' and '.join(exclusive)} are mutually exclusive",
        )

    # Authentication.
    try:
        creds = load_credentials()
    except AuthError as e:
        click.echo(f"auth: {e}", err=True)
        sys.exit(2)
    api = GdocAPI(creds)

    if plan_only:
        doc_id = None
        if doc:
            doc_id, _ = resolve_doc_target(doc)
        plan = push_dry_run(path, doc_id=doc_id, docs_service=api._docs if doc_id else None)
        write_plan_json(plan, plan_only)
        click.echo(f"wrote {plan_only}")
        click.echo(format_plan_summary(plan))
        return

    if dry_run:
        doc_id = None
        if doc:
            doc_id, _ = resolve_doc_target(doc)
        plan = push_dry_run(path, doc_id=doc_id, docs_service=api._docs if doc_id else None)
        click.echo(format_plan_summary(plan))
        return

    if new_doc:
        if not title:
            # Fall back to the frontmatter title
            from google_doc_diff.parse.markdown import parse_frontmatter
            fm, _ = parse_frontmatter(path.read_text())
            title = fm.get("title") or path.stem
        click.echo(f"creating new doc: {title!r}")
        result = push_new(
            path, title=title,
            drive_service=api._drive_v3, docs_service=api._docs,
        )
        click.echo(f"new doc id: {result.doc_id}")
        click.echo(format_plan_summary(result.plan))
        click.echo("ok")
        return

    if not doc:
        raise click.ClickException("either DOC or --new is required")
    doc_id, _ = resolve_doc_target(doc)
    if force:
        click.echo(f"pushing to {doc_id} (force) …")
        result = push_force(path, doc_id=doc_id, docs_service=api._docs)
        click.echo(format_plan_summary(result.plan))
        click.echo("ok")
        return

    if continue_:
        click.echo(f"pushing to {doc_id} (continue) …")
        try:
            result = push_continue(path, doc_id=doc_id, docs_service=api._docs)
        except UnresolvedConflictError as e:
            click.echo(f"  {e}", err=True)
            sys.exit(2)
        click.echo(format_plan_summary(result.plan))
        click.echo("ok")
        return

    if abort:
        click.echo(f"aborting {doc_id} push …")
        try:
            document, docs_json = _pull_rich_document_with_raw(api, doc_id)
        except Exception as e:
            click.echo(f"api: {e}", err=True)
            sys.exit(2)
        try:
            push_abort(path, document=document, docs_json=docs_json)
        except NoConflictToAbortError as e:
            click.echo(f"  {e}", err=True)
            sys.exit(2)
        click.echo(f"  restored {path} from remote; sidecar refreshed")
        return

    click.echo(f"pushing to {doc_id} (merge) …")
    result = push_merge(path, doc_id=doc_id, docs_service=api._docs)
    if result.conflicts:
        click.echo(
            f"  {len(result.conflicts)} conflict(s) — rewrote {path} with "
            "conflict markers; resolve and re-run `gdoc push --continue`.",
            err=True,
        )
        sys.exit(2)
    click.echo(format_plan_summary(result.plan))
    click.echo("ok")


if __name__ == "__main__":
    cli()
