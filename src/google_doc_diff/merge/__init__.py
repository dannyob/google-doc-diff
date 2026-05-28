"""Three-way AST merge.

Combines base, local, and remote ASTs into a single merged AST plus a
list of unresolved conflicts. The CLI uses this to give `gdoc push` a
fetch-and-merge default path (with `--force` retained for the
overwrite-anyway escape hatch).
"""

from google_doc_diff.merge.three_way import merge

__all__ = ["merge"]
