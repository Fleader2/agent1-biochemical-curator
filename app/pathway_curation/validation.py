"""Input validation for the Pathway Curation Planner.

``PathwayCurationRequest.__post_init__`` already rejects the most basic
malformations (empty ``request_id``/``organism_text``/
``biological_process``, non-positive budgets, an ``exclusions``/
``seed_entity_texts`` overlap). This module adds the one check that
depends on a relationship the dataclass itself cannot express cleanly:
whether the requested ``completion_policy`` is achievable given which
``include_*`` capabilities the same request enabled.
"""

from __future__ import annotations

from app.pathway_curation.errors import InvalidCurationRequestError
from app.pathway_curation.types import CompletionPolicy, PathwayCurationRequest


def require_valid_request(request: PathwayCurationRequest) -> PathwayCurationRequest:
    """Raise ``InvalidCurationRequestError`` if ``request`` cannot bound a search.

    Returns ``request`` unchanged on success, so callers can use this as
    a validating pass-through. Never raised for a request that is merely
    narrow or ambitious -- only for one that asks for something it has
    simultaneously disabled.
    """
    if not isinstance(request, PathwayCurationRequest):
        raise InvalidCurationRequestError(f"expected a PathwayCurationRequest, got {request!r}")

    if (
        request.completion_policy is CompletionPolicy.STRUCTURAL_EVIDENCE_AND_KINETICS
        and not request.include_kinetics
    ):
        raise InvalidCurationRequestError(
            "completion_policy=STRUCTURAL_EVIDENCE_AND_KINETICS requires include_kinetics=True -- "
            "the policy cannot be satisfied by a run that never attempts kinetics"
        )

    evidence_or_higher = (
        CompletionPolicy.STRUCTURAL_AND_EVIDENCE,
        CompletionPolicy.STRUCTURAL_EVIDENCE_AND_KINETICS,
    )
    if request.completion_policy in evidence_or_higher and not request.include_publications:
        raise InvalidCurationRequestError(
            f"completion_policy={request.completion_policy.value} requires "
            "include_publications=True -- the policy cannot be satisfied by a run that never "
            "attempts publication/evidence enrichment"
        )

    return request


__all__ = ["require_valid_request"]
