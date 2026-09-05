"""Small, deterministic, synthetic ``EvidenceExtraction`` fixtures for ``tests/claim_generation``.

Built directly on top of ``app.extraction`` (Increment 12) so these tests
exercise the real pipeline boundary, not a hand-rolled stand-in for it. No
live paper, no external API, no network access.
"""

from __future__ import annotations

from app.extraction.extractor import extract_evidence
from app.extraction.types import CandidateStatement, Directness, EvidenceExtraction
from app.models.enums import EvidenceType, SourceType


def _extraction(text: str, **candidate_overrides) -> EvidenceExtraction:
    defaults = {
        "quoted_text": text,
        "subject_text": "SubjectX",
        "predicate_text": "does something to",
        "evidence_type": EvidenceType.DIRECT_BIOCHEMICAL,
        "directness": Directness.AUTHORS_OBSERVED,
    }
    defaults.update(candidate_overrides)
    candidate = CandidateStatement(**defaults)
    [extraction] = extract_evidence(
        source=SourceType.PUBMED, source_identifier="PMID:1", text=text, candidates=[candidate]
    )
    return extraction


ACTIVATION_TEXT = "FadR activates fabA transcription in Escherichia coli."
ACTIVATION_EXTRACTION = _extraction(
    ACTIVATION_TEXT,
    subject_text="FadR",
    predicate_text="activates",
    object_text="fabA transcription",
    organism_text="Escherichia coli",
)

INHIBITION_TEXT = "Acyl-CoA inhibits FadR DNA binding activity."
INHIBITION_EXTRACTION = _extraction(
    INHIBITION_TEXT,
    subject_text="Acyl-CoA",
    predicate_text="inhibits",
    object_text="FadR DNA binding activity",
)

BINDING_TEXT = "FadR binds directly to the fabA promoter."
BINDING_EXTRACTION = _extraction(
    BINDING_TEXT,
    subject_text="FadR",
    predicate_text="binds directly to",
    object_text="the fabA promoter",
)

LOCALIZATION_TEXT = "GFP-tagged FadD is localized to the peroxisomal membrane."
LOCALIZATION_EXTRACTION = _extraction(
    LOCALIZATION_TEXT,
    subject_text="GFP-tagged FadD",
    predicate_text="is localized to",
    object_text=None,
    compartment_text="peroxisomal membrane",
    evidence_type=EvidenceType.LOCALIZATION,
)

ENZYME_REACTION_TEXT = "FadD catalyzes the conversion of a fatty acid to acyl-CoA."
ENZYME_REACTION_EXTRACTION = _extraction(
    ENZYME_REACTION_TEXT,
    subject_text="FadD",
    predicate_text="catalyzes the conversion of",
    object_text="a fatty acid to acyl-CoA",
)

COMPOUND_MEASUREMENT_TEXT = "The intracellular concentration of acyl-CoA was 3.2 mM."
COMPOUND_MEASUREMENT_EXTRACTION = _extraction(
    COMPOUND_MEASUREMENT_TEXT,
    subject_text="acyl-CoA",
    predicate_text="had intracellular concentration of",
    object_text=None,
    measured_quantity="intracellular concentration",
    measurement_value="3.2",
    measurement_units="mM",
)

REVIEW_TEXT = (
    "Fatty acid activation may proceed through several redundant acyl-CoA synthetases."
)
REVIEW_EXTRACTION = _extraction(
    REVIEW_TEXT,
    subject_text="fatty acid activation",
    predicate_text="may proceed through",
    object_text="several redundant acyl-CoA synthetases",
    evidence_type=EvidenceType.REVIEW,
    directness=Directness.REVIEW_SUMMARIZES,
)

NEGATIVE_FINDING_TEXT = "FadR did not bind the fabB promoter under these conditions."
NEGATIVE_FINDING_EXTRACTION = _extraction(
    NEGATIVE_FINDING_TEXT,
    subject_text="FadR",
    predicate_text="did not bind",
    object_text="the fabB promoter",
    qualifier_text="under these conditions",
)

MULTIPLE_PREDICATES_TEXT = "FadR activates fabA and inhibits fabB."
MULTIPLE_PREDICATES_EXTRACTIONS = tuple(
    extract_evidence(
        source=SourceType.PUBMED,
        source_identifier="PMID:2",
        text=MULTIPLE_PREDICATES_TEXT,
        candidates=[
            CandidateStatement(
                quoted_text=MULTIPLE_PREDICATES_TEXT,
                subject_text="FadR",
                predicate_text="activates",
                object_text="fabA",
                evidence_type=EvidenceType.TRANSCRIPTOMICS,
                directness=Directness.AUTHORS_OBSERVED,
                character_start=0,
                character_end=len(MULTIPLE_PREDICATES_TEXT),
            ),
            CandidateStatement(
                quoted_text=MULTIPLE_PREDICATES_TEXT,
                subject_text="FadR",
                predicate_text="inhibits",
                object_text="fabB",
                evidence_type=EvidenceType.TRANSCRIPTOMICS,
                directness=Directness.AUTHORS_OBSERVED,
                character_start=0,
                character_end=len(MULTIPLE_PREDICATES_TEXT),
            ),
        ],
    )
)

AMBIGUOUS_ENTITY_TEXT = "FadD hydrolyzed the substrate in vitro."
AMBIGUOUS_ENTITY_EXTRACTION = _extraction(
    AMBIGUOUS_ENTITY_TEXT,
    subject_text="FadD",
    predicate_text="hydrolyzed",
    object_text="the substrate",
    qualifier_text="in vitro",
)

MISSING_ORGANISM_TEXT = "FadD hydrolyzed long-chain acyl-CoA esters in a cell-free extract."
MISSING_ORGANISM_EXTRACTION = _extraction(
    MISSING_ORGANISM_TEXT,
    subject_text="FadD",
    predicate_text="hydrolyzed",
    object_text="long-chain acyl-CoA esters",
    organism_text=None,
)

MISSING_OBJECT_TEXT = "FadD is essential for growth on oleate."
MISSING_OBJECT_EXTRACTION = _extraction(
    MISSING_OBJECT_TEXT,
    subject_text="FadD",
    predicate_text="is essential for growth on oleate",
    object_text=None,
)

NUMERIC_VALUE_TEXT = "The specific activity of purified FadD was 12.3 nmol/min/mg."
NUMERIC_VALUE_EXTRACTION = _extraction(
    NUMERIC_VALUE_TEXT,
    subject_text="FadD",
    predicate_text="had specific activity of",
    object_text=None,
    measured_quantity="specific activity",
    measurement_value="12.3",
    measurement_units="nmol/min/mg",
)

LITERAL_VALUE_TEXT = "GeneA showed a 5.4-fold increase in expression level."
LITERAL_VALUE_EXTRACTION = _extraction(
    LITERAL_VALUE_TEXT,
    subject_text="GeneA",
    predicate_text="showed increase in expression level of",
    object_text=None,
    measured_quantity="expression level",
    measurement_value="5.4-fold",
)
