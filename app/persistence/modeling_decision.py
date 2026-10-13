"""ModelingDecision/ModelingDecisionOption persistence.

**Decision.** Idempotent on ``(project_id, subproject_id, model_id,
question)`` -- a repeat call with the identical context and question
reuses the existing decision rather than duplicating it. No database
uniqueness backs this (the context columns are nullable and a composite
unique index across three nullable columns plus free text would be of
limited value), so this is enforced at the application level, the same
"query before insert" idempotency ``app.persistence.model_specification``'s
child rows already use.

**Option.** Idempotent on ``(decision_id, label)``.

**Resolution.** ``resolve_modeling_decision`` is the only function that
updates an existing row in this module: it sets ``selected_option_id``,
``status``, ``resolution_source``, and ``rationale`` on an already-persisted
decision. It is never implicitly called by ``persist_modeling_decision`` --
a decision starts ``OPEN`` and is resolved only by an explicit, separate
call, mirroring ``app.persistence.knowledge_gap``'s "no automatic reopen or
resolve workflow" precedent. ``resolve_modeling_decision`` validates that
the selected option actually belongs to the decision being resolved --
cross-decision selection is rejected, never silently accepted.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import (
    ConfidenceClass,
    ModelingDecisionResolutionSource,
    ModelingDecisionStatus,
)
from app.models.modeling_decision import ModelingDecision, ModelingDecisionOption
from app.persistence.modeling_decision_types import (
    ModelingDecisionOptionPersistenceResult,
    ModelingDecisionPersistenceResult,
)
from app.persistence.types import PersistenceAction


def _existing_decision(
    session: Session,
    *,
    project_id: UUID | None,
    subproject_id: UUID | None,
    model_id: UUID | None,
    question: str,
) -> ModelingDecision | None:
    return session.execute(
        select(ModelingDecision).where(
            ModelingDecision.project_id == project_id,
            ModelingDecision.subproject_id == subproject_id,
            ModelingDecision.model_id == model_id,
            ModelingDecision.question == question,
        )
    ).scalar_one_or_none()


def persist_modeling_decision(
    *,
    question: str,
    session: Session,
    project_id: UUID | None = None,
    subproject_id: UUID | None = None,
    model_id: UUID | None = None,
    decision_type: str | None = None,
    notes: str | None = None,
) -> ModelingDecisionPersistenceResult:
    """Persist one ``ModelingDecision``, starting ``OPEN``. See module docstring."""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("persist_modeling_decision requires a non-empty question")
    if project_id is None and subproject_id is None and model_id is None:
        raise ValueError(
            "persist_modeling_decision requires at least one of project_id/subproject_id/model_id"
        )

    existing = _existing_decision(
        session,
        project_id=project_id,
        subproject_id=subproject_id,
        model_id=model_id,
        question=question,
    )
    if existing is not None:
        return ModelingDecisionPersistenceResult(
            action=PersistenceAction.REUSED_EXISTING,
            decision_id=existing.id,
            created=False,
            reused_existing=True,
            question=question,
            reason=(
                f"reused existing modeling decision {existing.id} with the identical "
                "context and question"
            ),
        )

    try:
        with session.begin_nested():
            row = ModelingDecision(
                project_id=project_id,
                subproject_id=subproject_id,
                model_id=model_id,
                question=question,
                decision_type=decision_type,
                status=ModelingDecisionStatus.OPEN,
                notes=notes,
            )
            session.add(row)
            session.flush()
    except IntegrityError:
        raced_existing = _existing_decision(
            session,
            project_id=project_id,
            subproject_id=subproject_id,
            model_id=model_id,
            question=question,
        )
        if raced_existing is not None:
            return ModelingDecisionPersistenceResult(
                action=PersistenceAction.REUSED_EXISTING,
                decision_id=raced_existing.id,
                created=False,
                reused_existing=True,
                question=question,
                reason="reused existing modeling decision after a concurrent insert won",
            )
        return ModelingDecisionPersistenceResult(
            action=PersistenceAction.FAILED,
            decision_id=None,
            created=False,
            reused_existing=False,
            question=question,
            reason=(
                "modeling decision creation rolled back due to a database integrity "
                "violation, and no matching row was found on recheck"
            ),
        )

    return ModelingDecisionPersistenceResult(
        action=PersistenceAction.CREATED,
        decision_id=row.id,
        created=True,
        reused_existing=False,
        question=question,
        reason="created new modeling decision",
    )


def add_modeling_decision_option(
    *,
    decision_id: UUID,
    label: str,
    session: Session,
    description: str | None = None,
    evidence_summary: str | None = None,
    confidence_score: float | None = None,
    confidence_class: ConfidenceClass | None = None,
    consequence_summary: str | None = None,
    recommended: bool = False,
) -> ModelingDecisionOptionPersistenceResult:
    """Add one candidate option to a ``ModelingDecision``. See module docstring."""
    if not isinstance(label, str) or not label.strip():
        raise ValueError("add_modeling_decision_option requires a non-empty label")

    where_clauses = (
        ModelingDecisionOption.decision_id == decision_id,
        ModelingDecisionOption.label == label,
    )
    existing = session.execute(
        select(ModelingDecisionOption).where(*where_clauses)
    ).scalar_one_or_none()
    if existing is not None:
        return ModelingDecisionOptionPersistenceResult(
            action=PersistenceAction.REUSED_EXISTING,
            option_id=existing.id,
            created=False,
            reused_existing=True,
            label=label,
            reason=f"reused existing option {existing.id} with the identical (decision_id, label)",
        )

    try:
        with session.begin_nested():
            row = ModelingDecisionOption(
                decision_id=decision_id,
                label=label,
                description=description,
                evidence_summary=evidence_summary,
                confidence_score=confidence_score,
                confidence_class=confidence_class,
                consequence_summary=consequence_summary,
                recommended=recommended,
            )
            session.add(row)
            session.flush()
    except IntegrityError:
        raced_existing = session.execute(
            select(ModelingDecisionOption).where(*where_clauses)
        ).scalar_one_or_none()
        if raced_existing is not None:
            return ModelingDecisionOptionPersistenceResult(
                action=PersistenceAction.REUSED_EXISTING,
                option_id=raced_existing.id,
                created=False,
                reused_existing=True,
                label=label,
                reason="reused existing option after a concurrent insert won",
            )
        return ModelingDecisionOptionPersistenceResult(
            action=PersistenceAction.FAILED,
            option_id=None,
            created=False,
            reused_existing=False,
            label=label,
            reason=(
                "option creation rolled back due to a database integrity violation, "
                "and no matching row was found on recheck"
            ),
        )

    return ModelingDecisionOptionPersistenceResult(
        action=PersistenceAction.CREATED,
        option_id=row.id,
        created=True,
        reused_existing=False,
        label=label,
        reason="created new option",
    )


def resolve_modeling_decision(
    *,
    decision_id: UUID,
    selected_option_id: UUID,
    resolution_source: ModelingDecisionResolutionSource,
    session: Session,
    rationale: str | None = None,
) -> ModelingDecision:
    """Mark a ``ModelingDecision`` ``RESOLVED`` with its selected option.

    Raises ``ValueError`` if the decision does not exist, the option does
    not exist, or the option does not belong to this decision -- a
    cross-decision selection is never silently accepted.
    """
    decision = session.get(ModelingDecision, decision_id)
    if decision is None:
        raise ValueError(f"no ModelingDecision with id {decision_id}")
    option = session.get(ModelingDecisionOption, selected_option_id)
    if option is None:
        raise ValueError(f"no ModelingDecisionOption with id {selected_option_id}")
    if option.decision_id != decision_id:
        raise ValueError(
            f"ModelingDecisionOption {selected_option_id} belongs to decision "
            f"{option.decision_id}, not {decision_id}"
        )

    decision.selected_option_id = selected_option_id
    decision.status = ModelingDecisionStatus.RESOLVED
    decision.resolution_source = resolution_source
    if rationale is not None:
        decision.rationale = rationale
    session.flush()
    return decision


def get_modeling_decision(session: Session, decision_id: UUID) -> ModelingDecision | None:
    """One ``ModelingDecision`` row by id, or ``None``. Read-only."""
    return session.get(ModelingDecision, decision_id)


__all__ = [
    "add_modeling_decision_option",
    "get_modeling_decision",
    "persist_modeling_decision",
    "resolve_modeling_decision",
]
