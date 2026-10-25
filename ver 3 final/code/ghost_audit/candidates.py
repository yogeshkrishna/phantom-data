"""Isolated semantic experiments with explicit, context-local selection.

Candidate correctness on synthetic inputs does not establish hidden-data accuracy.
Only candidates reviewed by the release evaluation belong in DEFAULT_CANDIDATES.
An explicit empty scope always restores the unmodified Version 2 semantics.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

CANDIDATES = {
    "marker_content": "Inspect observed prohibited content before accepting a clean-state marker.",
    "projection_completeness": "Require explicit field coverage before inferring clean contents from omissions.",
    "reference_fields": "Recognize attribution synonyms and identifier-bearing message excerpts.",
    "independent_copies": "Track the latest state separately for each explicitly identified physical copy.",
    "snapshot_coverage": "Require explicit exhaustive, matching coverage for absence by omission.",
    "temporal_categories": "Select category and policy only from observations at or before the audit cutoff.",
    "temporal_identity": "Use identity and ownership information available at each case cutoff.",
    "document_rows": "Keep document contents, identifiers, and explicit absence local to each resource row.",
    "citation_continuity": "Do not reuse a clean citation across an intervening contradictory observation.",
    "intent_scope": "Distinguish actual deletion of personal data from side requests and hypothetical questions.",
    "completion_proof": "Treat an authorized request with no resource proof as unestablished completion.",
    "fractional_timestamps": "Preserve fractional timestamp precision at audit and retention cutoffs.",
}

# Each promoted candidate passed isolated counterexamples/controls and preserved
# the visible sample and all 518 current prediction rows. This is not a claim
# about unseen canonical accuracy; rejected candidates remain explicitly opt-in.
DEFAULT_CANDIDATES: tuple[str, ...] = (
    "marker_content", "reference_fields", "temporal_categories",
    "citation_continuity", "intent_scope", "completion_proof", "fractional_timestamps",
)
_selection: ContextVar[frozenset[str] | None] = ContextVar("ghost_audit_candidates", default=None)


def enabled(name: str) -> bool:
    if name not in CANDIDATES:
        raise ValueError(f"unknown semantic candidate: {name}")
    selected = _selection.get()
    return name in (DEFAULT_CANDIDATES if selected is None else selected)


@contextmanager
def candidate_scope(names):
    """Select exactly these candidates for one run, restoring the caller on exit."""
    names = None if names is None else frozenset(names)
    unknown = set() if names is None else names - CANDIDATES.keys()
    if unknown:
        raise ValueError(f"unknown semantic candidates: {', '.join(sorted(unknown))}")
    token = _selection.set(names)
    try:
        yield
    finally:
        _selection.reset(token)
