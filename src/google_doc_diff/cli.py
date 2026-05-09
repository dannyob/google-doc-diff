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
def pull(doc, out, html_out, extract_assets, revision):
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
