"""Command-line interface for gdoc."""

import click

from google_doc_diff import __version__


@click.group()
@click.version_option(version=__version__, prog_name="gdoc")
def cli():
    """Pull Google Docs into high-fidelity Markdown and HTML."""
    pass


if __name__ == "__main__":
    cli()
