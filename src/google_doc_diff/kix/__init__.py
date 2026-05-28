"""Kix enrichment layer — optional read-side decoration via Chrome cookies."""

from google_doc_diff.kix.auth import KixSession, kix_available, load_kix_session
from google_doc_diff.kix.enrich import EnrichResult, enrich_from_kix
from google_doc_diff.kix.model import KixModel, extract_ot_ops

__all__ = [
    "EnrichResult",
    "KixModel",
    "KixSession",
    "enrich_from_kix",
    "extract_ot_ops",
    "kix_available",
    "load_kix_session",
]
