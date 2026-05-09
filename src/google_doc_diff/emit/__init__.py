"""Public emit surface."""

from google_doc_diff.emit.html import emit_document_html
from google_doc_diff.emit.markdown import emit_document_md

__all__ = ["emit_document_md", "emit_document_html"]
