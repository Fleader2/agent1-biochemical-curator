"""Enzyme regulatory state identity: pure reshaping and deterministic signatures.

Read-only, decision-only, deterministic, source-neutral (Increment B
instructions, Step 20) -- nothing here writes to the database, and there is
no ``Session`` anywhere in this module. Consolidated into one file (rather
than four) because the four identities are interdependent
(``EnzymeStateIdentity`` embeds ``ModificationIdentity``/
``AllostericLigandIdentity`` to compute its own signature) -- Increment B's
own instructions explicitly permit "a cohesive smaller module" in place of
one file per persistence target (Step 24), and the same reasoning applies
to normalization.

**No ``NormalizationResult``/``NormalizationStatus``/``*Lookup`` protocol
exists here**, unlike ``app.normalization.reaction_enzyme``/``.compound``/
etc. Those exist because their tables permit at most one canonical row per
identity and a genuine MATCHED/NEW/AMBIGUOUS/CONFLICTED decision must be
made against already-existing candidates fetched from the database. Every
table here is instead **content-addressable**: identical curated content
always produces the identical ``identity_key`` (a deterministic SHA-256
digest, mirroring ``app.persistence.knowledge_gap.compute_identity_key``'s
own precedent, extended to four tables), so there is no candidate-fetching
decision to make -- ``app.persistence.enzyme_state`` looks up by
``identity_key`` directly and either reuses or creates, exactly as
``knowledge_gap`` already does for its own single table.

**Never resolves entity identity itself.** ``parent_id``/
``modifying_compound_id``/``ligand_compound_id``/``compartment_id``/
``from_state_id``/``to_state_id``/``reaction_id``/``publication_id``/
``evidence_id`` are accepted only as already-normalized UUIDs supplied by
the caller (Increment B instructions, Step 1/18-23's discipline, carried
over unchanged from Increment A) -- this module never looks up a protein,
complex, compound, or compartment by name or any other weak signal.

**Identity is fixed at construction, never recomputed from a live
database query.** ``EnzymeStateIdentity.modifications``/
``.allosteric_ligands`` describe the *complete, intended* child-row set
the caller is about to persist alongside the state -- this module has no
way to see what a caller will persist later, so it cannot verify the two
match; that is a documented caller responsibility
(``docs/25_enzyme_regulatory_states_contract.md`` §16), mirroring how
``app.persistence.reaction`` already requires participants to be persisted
in the same logical batch as their reaction.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from app.models.enums import (
    AllostericEffect,
    EnzymeStateTransitionType,
    EnzymeStateType,
    ModificationType,
    SourceType,
)
from app.normalization.identifiers import require_non_empty

_ENZYME_STATE_IDENTITY_VERSION = "es-v1"
_ENZYME_MODIFICATION_IDENTITY_VERSION = "em-v1"
_ALLOSTERIC_INTERACTION_IDENTITY_VERSION = "ai-v1"
_ENZYME_STATE_TRANSITION_IDENTITY_VERSION = "est-v1"


class EnzymeStateParentType(StrEnum):
    """Which kind of macromolecule an ``EnzymeState`` describes.

    A local-only vocabulary (not a database enum): it exists purely to let
    ``EnzymeStateIdentity`` express "exactly one of protein/complex"
    without a pair of optional UUID fields duplicating
    ``EnzymeState.protein_id``/``.complex_id``'s own XOR at this layer too.
    """

    PROTEIN = "PROTEIN"
    COMPLEX = "COMPLEX"


def _uuid_str(value: UUID | None) -> str | None:
    return str(value) if value is not None else None


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha256_digest(canonical: object) -> str:
    return hashlib.sha256(_canonical_json(canonical).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ModificationIdentity:
    """One intended covalent/post-translational modification, as part of a state's signature.

    ``stoichiometry`` participates in identity (Increment B instructions,
    Step 21: "A state with one phosphate must not collapse with two ...
    if multiplicity is known") -- a singly- and doubly-modified form of the
    identical residue are different states whenever stoichiometry is
    actually known. ``notes`` never participates in identity (free-text
    metadata, the same "explanation text does not define identity"
    convention ``app.persistence.knowledge_gap`` already established).
    """

    modification_type: ModificationType
    residue: str | None = None
    residue_position: int | None = None
    site_label: str | None = None
    modifying_compound_id: UUID | None = None
    stoichiometry: Decimal | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.modification_type, ModificationType):
            raise TypeError(
                f"ModificationIdentity.modification_type must be a ModificationType, "
                f"got {self.modification_type!r}"
            )
        if self.residue_position is not None and not isinstance(self.residue_position, int):
            raise TypeError(
                f"ModificationIdentity.residue_position must be an int or None, "
                f"got {self.residue_position!r}"
            )
        if self.stoichiometry is not None and not isinstance(self.stoichiometry, Decimal):
            raise TypeError(
                f"ModificationIdentity.stoichiometry must be a Decimal or None, "
                f"got {self.stoichiometry!r}"
            )

    def _canonical(self) -> dict[str, object]:
        return {
            "modification_type": self.modification_type.value,
            "residue": self.residue,
            "residue_position": self.residue_position,
            "site_label": self.site_label,
            "modifying_compound_id": _uuid_str(self.modifying_compound_id),
            "stoichiometry": str(self.stoichiometry) if self.stoichiometry is not None else None,
        }


@dataclass(frozen=True, slots=True)
class AllostericLigandIdentity:
    """One intended allosteric ligand relationship, as part of a state's signature.

    ``ligand_compound_id`` is required -- an unresolved ligand cannot
    participate in a state's identity at all (Increment B instructions,
    Step 9: never identify a ligand solely by free-text name).
    ``mechanism``/``notes`` never participate in identity (free-text
    metadata only).
    """

    ligand_compound_id: UUID
    effect: AllostericEffect
    site_label: str | None = None
    mechanism: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if self.ligand_compound_id is None:
            raise ValueError("AllostericLigandIdentity requires ligand_compound_id")
        if not isinstance(self.effect, AllostericEffect):
            raise TypeError(
                f"AllostericLigandIdentity.effect must be an AllostericEffect, got {self.effect!r}"
            )

    def _canonical(self) -> dict[str, object]:
        return {
            "ligand_compound_id": _uuid_str(self.ligand_compound_id),
            "effect": self.effect.value,
            "site_label": self.site_label,
        }


@dataclass(frozen=True, slots=True)
class EnzymeStateIdentity:
    """Source-neutral description of one enzyme regulatory state, complete with its
    intended modification/allosteric-ligand set.

    Requires exactly one of ``parent_type=PROTEIN`` with a protein
    ``parent_id`` or ``parent_type=COMPLEX`` with a complex ``parent_id``
    -- there is only one ``parent_id`` field, disambiguated by
    ``parent_type``, rather than two optional UUID fields (never
    conflating ``Protein``/``EnzymeComplex`` identity with state identity,
    Increment B instructions, Step 3).
    """

    source: SourceType
    source_identifier: str
    parent_type: EnzymeStateParentType
    parent_id: UUID
    state_type: EnzymeStateType

    compartment_id: UUID | None = None
    modifications: tuple[ModificationIdentity, ...] = ()
    allosteric_ligands: tuple[AllostericLigandIdentity, ...] = ()

    state_label: str | None = None
    active_state: bool | None = None

    publication_id: UUID | None = None
    evidence_id: UUID | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_identifier",
            require_non_empty(self.source_identifier, field_name="source_identifier"),
        )
        if not isinstance(self.parent_type, EnzymeStateParentType):
            raise TypeError(
                f"EnzymeStateIdentity.parent_type must be an EnzymeStateParentType, "
                f"got {self.parent_type!r}"
            )
        if self.parent_id is None:
            raise ValueError("EnzymeStateIdentity requires parent_id")
        if not isinstance(self.state_type, EnzymeStateType):
            raise TypeError(
                f"EnzymeStateIdentity.state_type must be an EnzymeStateType, "
                f"got {self.state_type!r}"
            )
        if not isinstance(self.modifications, tuple) or any(
            not isinstance(m, ModificationIdentity) for m in self.modifications
        ):
            raise TypeError(
                "EnzymeStateIdentity.modifications must be a tuple of ModificationIdentity"
            )
        if not isinstance(self.allosteric_ligands, tuple) or any(
            not isinstance(a, AllostericLigandIdentity) for a in self.allosteric_ligands
        ):
            raise TypeError(
                "EnzymeStateIdentity.allosteric_ligands must be a tuple of AllostericLigandIdentity"
            )


def compute_enzyme_state_identity_key(identity: EnzymeStateIdentity) -> str:
    """A deterministic, versioned digest of ``identity``'s defining fields.

    Never Python's built-in ``hash()``. ``modifications``/
    ``allosteric_ligands`` are each rendered to their own canonical JSON
    dict and then sorted by that dict's own canonical string -- a total,
    deterministic order requiring no custom comparator and no ``hash()``,
    so input ordering never changes the result (Increment B instructions,
    Step 23). ``state_label``/``active_state``/``notes``/``source``/
    ``source_identifier``/``publication_id``/``evidence_id`` never
    participate: metadata, not identity.
    """
    modification_dicts = sorted(
        (m._canonical() for m in identity.modifications), key=_canonical_json
    )
    ligand_dicts = sorted(
        (a._canonical() for a in identity.allosteric_ligands), key=_canonical_json
    )
    canonical = {
        "parent_type": identity.parent_type.value,
        "parent_id": _uuid_str(identity.parent_id),
        "state_type": identity.state_type.value,
        "compartment_id": _uuid_str(identity.compartment_id),
        "modifications": modification_dicts,
        "allosteric_ligands": ligand_dicts,
    }
    return f"{_ENZYME_STATE_IDENTITY_VERSION}:{_sha256_digest(canonical)}"


@dataclass(frozen=True, slots=True)
class EnzymeModificationIdentity:
    """Source-neutral description of one modification attached to an *existing* ``EnzymeState``.

    ``enzyme_state_id`` is the already-persisted parent state's real id --
    this identity is for attaching an additional, individually-persisted
    ``EnzymeModification`` row (see module docstring: identity for the
    *parent state* is computed once, at its own creation, from the
    complete set the caller supplied then; this is the per-row identity
    used by ``app.persistence.enzyme_state.persist_enzyme_modification``).
    """

    source: SourceType
    source_identifier: str
    enzyme_state_id: UUID
    modification_type: ModificationType

    residue: str | None = None
    residue_position: int | None = None
    site_label: str | None = None
    modifying_compound_id: UUID | None = None
    stoichiometry: Decimal | None = None

    publication_id: UUID | None = None
    evidence_id: UUID | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_identifier",
            require_non_empty(self.source_identifier, field_name="source_identifier"),
        )
        if self.enzyme_state_id is None:
            raise ValueError("EnzymeModificationIdentity requires enzyme_state_id")
        if not isinstance(self.modification_type, ModificationType):
            raise TypeError(
                f"EnzymeModificationIdentity.modification_type must be a ModificationType, "
                f"got {self.modification_type!r}"
            )
        if self.stoichiometry is not None and not isinstance(self.stoichiometry, Decimal):
            raise TypeError(
                f"EnzymeModificationIdentity.stoichiometry must be a Decimal or None, "
                f"got {self.stoichiometry!r}"
            )


def compute_enzyme_modification_identity_key(identity: EnzymeModificationIdentity) -> str:
    """A deterministic, versioned digest of one modification's defining fields."""
    canonical = {
        "enzyme_state_id": _uuid_str(identity.enzyme_state_id),
        "modification_type": identity.modification_type.value,
        "residue": identity.residue,
        "residue_position": identity.residue_position,
        "site_label": identity.site_label,
        "modifying_compound_id": _uuid_str(identity.modifying_compound_id),
        "stoichiometry": str(identity.stoichiometry)
        if identity.stoichiometry is not None
        else None,
    }
    return f"{_ENZYME_MODIFICATION_IDENTITY_VERSION}:{_sha256_digest(canonical)}"


@dataclass(frozen=True, slots=True)
class AllostericInteractionIdentity:
    """Source-neutral description of one allosteric interaction attached to an
    *existing* ``EnzymeState``. See ``EnzymeModificationIdentity`` for why this
    is separate from the ligand set embedded in ``EnzymeStateIdentity``.
    """

    source: SourceType
    source_identifier: str
    enzyme_state_id: UUID
    ligand_compound_id: UUID
    effect: AllostericEffect

    site_label: str | None = None
    mechanism: str | None = None

    publication_id: UUID | None = None
    evidence_id: UUID | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_identifier",
            require_non_empty(self.source_identifier, field_name="source_identifier"),
        )
        if self.enzyme_state_id is None:
            raise ValueError("AllostericInteractionIdentity requires enzyme_state_id")
        if self.ligand_compound_id is None:
            raise ValueError("AllostericInteractionIdentity requires ligand_compound_id")
        if not isinstance(self.effect, AllostericEffect):
            raise TypeError(
                f"AllostericInteractionIdentity.effect must be an AllostericEffect, "
                f"got {self.effect!r}"
            )


def compute_allosteric_interaction_identity_key(identity: AllostericInteractionIdentity) -> str:
    """A deterministic, versioned digest of one allosteric interaction's defining fields."""
    canonical = {
        "enzyme_state_id": _uuid_str(identity.enzyme_state_id),
        "ligand_compound_id": _uuid_str(identity.ligand_compound_id),
        "effect": identity.effect.value,
        "site_label": identity.site_label,
    }
    return f"{_ALLOSTERIC_INTERACTION_IDENTITY_VERSION}:{_sha256_digest(canonical)}"


@dataclass(frozen=True, slots=True)
class EnzymeStateTransitionIdentity:
    """Source-neutral description of one transition between two existing ``EnzymeState`` rows.

    ``from_state_id``/``to_state_id`` must differ -- mirrors the database's
    own ``ck_enzyme_state_transition_distinct_states`` constraint at this
    layer too, so a violation is caught before ever reaching the database.
    """

    source: SourceType
    source_identifier: str
    from_state_id: UUID
    to_state_id: UUID
    transition_type: EnzymeStateTransitionType

    reaction_id: UUID | None = None
    publication_id: UUID | None = None
    evidence_id: UUID | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_identifier",
            require_non_empty(self.source_identifier, field_name="source_identifier"),
        )
        if self.from_state_id is None or self.to_state_id is None:
            raise ValueError("EnzymeStateTransitionIdentity requires from_state_id and to_state_id")
        if self.from_state_id == self.to_state_id:
            raise ValueError(
                "EnzymeStateTransitionIdentity.from_state_id and .to_state_id must differ"
            )
        if not isinstance(self.transition_type, EnzymeStateTransitionType):
            raise TypeError(
                f"EnzymeStateTransitionIdentity.transition_type must be an "
                f"EnzymeStateTransitionType, got {self.transition_type!r}"
            )


def compute_enzyme_state_transition_identity_key(identity: EnzymeStateTransitionIdentity) -> str:
    """A deterministic, versioned digest of one transition's defining fields."""
    canonical = {
        "from_state_id": _uuid_str(identity.from_state_id),
        "to_state_id": _uuid_str(identity.to_state_id),
        "transition_type": identity.transition_type.value,
        "reaction_id": _uuid_str(identity.reaction_id),
    }
    return f"{_ENZYME_STATE_TRANSITION_IDENTITY_VERSION}:{_sha256_digest(canonical)}"


__all__ = [
    "AllostericInteractionIdentity",
    "AllostericLigandIdentity",
    "EnzymeModificationIdentity",
    "EnzymeStateIdentity",
    "EnzymeStateParentType",
    "EnzymeStateTransitionIdentity",
    "ModificationIdentity",
    "compute_allosteric_interaction_identity_key",
    "compute_enzyme_modification_identity_key",
    "compute_enzyme_state_identity_key",
    "compute_enzyme_state_transition_identity_key",
]
