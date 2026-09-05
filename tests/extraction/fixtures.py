"""Small, deterministic, synthetic scientific-text fixtures for ``tests/extraction``.

None of this text is real -- no live paper, no external API, no network
access. Each fixture pairs a short synthetic passage with the
``CandidateStatement``(s) a human or LLM might extract from it, for tests
to feed into ``extract_evidence`` end to end.
"""

from __future__ import annotations

from app.extraction.types import CandidateStatement, Directness
from app.models.enums import EvidenceType

POSITIVE_FINDING_TEXT = (
    "Purified FadD hydrolyzed long-chain acyl-CoA esters in vitro, "
    "confirming its role as an acyl-CoA synthetase."
)
POSITIVE_FINDING_CANDIDATE = CandidateStatement(
    quoted_text=(
        "Purified FadD hydrolyzed long-chain acyl-CoA esters in vitro, "
        "confirming its role as an acyl-CoA synthetase."
    ),
    subject_text="FadD",
    predicate_text="hydrolyzed",
    object_text="long-chain acyl-CoA esters",
    qualifier_text="in vitro",
    evidence_type=EvidenceType.DIRECT_BIOCHEMICAL,
    directness=Directness.AUTHORS_OBSERVED,
)

NEGATIVE_RESULT_TEXT = (
    "No detectable beta-oxidation activity was observed in the fadD deletion "
    "strain under any tested carbon source."
)
NEGATIVE_RESULT_CANDIDATE = CandidateStatement(
    quoted_text=(
        "No detectable beta-oxidation activity was observed in the fadD "
        "deletion strain under any tested carbon source."
    ),
    subject_text="fadD deletion strain",
    predicate_text="showed no detectable",
    object_text="beta-oxidation activity",
    evidence_type=EvidenceType.GENETIC,
    directness=Directness.AUTHORS_OBSERVED,
    perturbation="fadD deletion",
)

MULTIPLE_FINDINGS_TEXT = (
    "FadD hydrolyzed long-chain acyl-CoA esters in vitro. "
    "FadL was required for uptake of exogenous fatty acids."
)
MULTIPLE_FINDINGS_CANDIDATES = (
    CandidateStatement(
        quoted_text="FadD hydrolyzed long-chain acyl-CoA esters in vitro.",
        subject_text="FadD",
        predicate_text="hydrolyzed",
        object_text="long-chain acyl-CoA esters",
        qualifier_text="in vitro",
        evidence_type=EvidenceType.DIRECT_BIOCHEMICAL,
        directness=Directness.AUTHORS_OBSERVED,
        sentence_index=0,
    ),
    CandidateStatement(
        quoted_text="FadL was required for uptake of exogenous fatty acids.",
        subject_text="FadL",
        predicate_text="was required for",
        object_text="uptake of exogenous fatty acids",
        evidence_type=EvidenceType.GENETIC,
        directness=Directness.AUTHORS_OBSERVED,
        sentence_index=1,
    ),
)

FIGURE_REFERENCE_TEXT = (
    "Overexpression of FadR increased fabA transcription, as shown in Figure 3B."
)
FIGURE_REFERENCE_CANDIDATE = CandidateStatement(
    quoted_text="Overexpression of FadR increased fabA transcription, as shown in Figure 3B.",
    subject_text="FadR overexpression",
    predicate_text="increased",
    object_text="fabA transcription",
    evidence_type=EvidenceType.TRANSCRIPTOMICS,
    directness=Directness.AUTHORS_OBSERVED,
    perturbation="FadR overexpression",
    figure_reference="Figure 3B",
)

MEASUREMENT_TEXT = (
    "The specific activity of purified FadD was 12.3 +/- 0.4 nmol/min/mg (p < 0.01, n = 3)."
)
MEASUREMENT_CANDIDATE = CandidateStatement(
    quoted_text=(
        "The specific activity of purified FadD was 12.3 +/- 0.4 nmol/min/mg (p < 0.01, n = 3)."
    ),
    subject_text="FadD",
    predicate_text="had specific activity of",
    evidence_type=EvidenceType.DIRECT_BIOCHEMICAL,
    directness=Directness.AUTHORS_OBSERVED,
    measured_quantity="specific activity",
    measurement_value="12.3 +/- 0.4",
    measurement_units="nmol/min/mg",
    statistical_support="p < 0.01, n = 3",
)

MUTATION_TEXT = "The fadD1 point mutation abolished acyl-CoA synthetase activity."
MUTATION_CANDIDATE = CandidateStatement(
    quoted_text="The fadD1 point mutation abolished acyl-CoA synthetase activity.",
    subject_text="fadD1 point mutation",
    predicate_text="abolished",
    object_text="acyl-CoA synthetase activity",
    evidence_type=EvidenceType.GENETIC,
    directness=Directness.AUTHORS_OBSERVED,
    perturbation="fadD1 point mutation",
)

COMPARTMENT_TEXT = "GFP-tagged FadD localized to the peroxisomal membrane."
COMPARTMENT_CANDIDATE = CandidateStatement(
    quoted_text="GFP-tagged FadD localized to the peroxisomal membrane.",
    subject_text="GFP-tagged FadD",
    predicate_text="localized to",
    object_text="the peroxisomal membrane",
    evidence_type=EvidenceType.LOCALIZATION,
    directness=Directness.AUTHORS_OBSERVED,
    compartment_text="peroxisomal membrane",
)

REVIEW_DISCUSSION_TEXT = (
    "Overall, fatty acid activation may proceed through several redundant "
    "acyl-CoA synthetases, though the relative contribution of each remains debated."
)
REVIEW_DISCUSSION_CANDIDATE = CandidateStatement(
    quoted_text=(
        "Overall, fatty acid activation may proceed through several redundant "
        "acyl-CoA synthetases, though the relative contribution of each remains debated."
    ),
    subject_text="fatty acid activation",
    predicate_text="may proceed through",
    object_text="several redundant acyl-CoA synthetases",
    evidence_type=EvidenceType.REVIEW,
    directness=Directness.REVIEW_SUMMARIZES,
)

AMBIGUOUS_WORDING_TEXT = (
    "FadD was purified alongside FadK; it retained full catalytic activity after storage."
)
AMBIGUOUS_WORDING_CANDIDATE = CandidateStatement(
    quoted_text=(
        "FadD was purified alongside FadK; it retained full catalytic activity after storage."
    ),
    subject_text="it",
    predicate_text="retained",
    object_text="full catalytic activity",
    qualifier_text="after storage",
    evidence_type=EvidenceType.DIRECT_BIOCHEMICAL,
    directness=Directness.AUTHORS_OBSERVED,
    notes="Pronoun antecedent (FadD vs FadK) is ambiguous in the source passage.",
)

MISSING_ORGANISM_TEXT = "FadD hydrolyzed long-chain acyl-CoA esters in a cell-free extract."
MISSING_ORGANISM_CANDIDATE = CandidateStatement(
    quoted_text="FadD hydrolyzed long-chain acyl-CoA esters in a cell-free extract.",
    subject_text="FadD",
    predicate_text="hydrolyzed",
    object_text="long-chain acyl-CoA esters",
    qualifier_text="cell-free extract",
    evidence_type=EvidenceType.DIRECT_BIOCHEMICAL,
    directness=Directness.AUTHORS_OBSERVED,
    organism_text=None,
)
