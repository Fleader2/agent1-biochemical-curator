"""SQLAlchemy ORM models.

Group A reference-data tables, Group B gene/protein/enzyme-complex tables,
Group C reaction tables, Group D claim/evidence tables, Group E's
kinetic_measurement table, Group F's regulatory_interaction/
modeling_assumption/knowledge_gap tables, Group G's external_record/
source_cross_reference/review_event tables, and the KEGG NETWORK support
increment's network/network_node/network_edge/network_perturbation/
network_pathway/network_disease/network_publication tables, and the
Project/Subproject/Model/ModelSpecification foundation increment's project/
project_regulatory_context/subproject/model/model_specification (+ its
eight child tables)/modeling_decision/modeling_decision_option tables
(``docs/02_database_schema.md``) are defined here. Submodules are imported
so that ``Base.metadata`` is fully populated as soon as ``app.models`` is
imported, which is what ``migrations/env.py`` relies on for Alembic to
discover table metadata. Remaining tables are added in later phases.
"""

from app.db.base import Base
from app.models.claim import Claim, Evidence, EvidenceCondition
from app.models.compartment import Compartment
from app.models.compound import Compound, CompoundSynonym
from app.models.enzyme_complex import EnzymeComplex, EnzymeComplexMember
from app.models.enzyme_state import (
    AllostericInteraction,
    EnzymeModification,
    EnzymeState,
    EnzymeStateTransition,
)
from app.models.experiment_execution import (
    ExperimentExecution,
    ExperimentExecutionEvent,
    ExperimentResult,
)
from app.models.experiment_recommendation import (
    ExperimentRecommendationEvent,
    ExperimentRecommendationRecord,
)
from app.models.experimental_condition import ExperimentalCondition
from app.models.experimental_context import ExperimentalContext
from app.models.external_record import ExternalRecord
from app.models.gene import Gene
from app.models.kinetic_measurement import KineticMeasurement, KineticMeasurementProteinContext
from app.models.knowledge_gap import KnowledgeGap
from app.models.model import Model
from app.models.model_specification import (
    ModelSpecification,
    ModelSpecificationCompartment,
    ModelSpecificationEnzymeState,
    ModelSpecificationGeneProtein,
    ModelSpecificationPathway,
    ModelSpecificationReaction,
    ModelSpecificationRegulatoryInteraction,
    ModelSpecificationSpecies,
)
from app.models.modeling_assumption import ModelingAssumption
from app.models.modeling_decision import ModelingDecision, ModelingDecisionOption
from app.models.network import (
    Network,
    NetworkDisease,
    NetworkEdge,
    NetworkNode,
    NetworkPathway,
    NetworkPerturbation,
    NetworkPublication,
)
from app.models.organism import Organism
from app.models.perturbation import Perturbation
from app.models.project import Project, ProjectRegulatoryContext
from app.models.protein import Protein
from app.models.publication import Publication
from app.models.quantitative_observation import (
    QuantitativeObservation,
    QuantitativeObservationDependency,
)
from app.models.reaction import Reaction, ReactionEnzyme, ReactionParticipant
from app.models.regulatory_interaction import RegulatoryInteraction
from app.models.review_event import ReviewEvent
from app.models.source_cross_reference import SourceCrossReference
from app.models.subproject import Subproject

__all__ = [
    "AllostericInteraction",
    "Base",
    "Claim",
    "Compartment",
    "Compound",
    "CompoundSynonym",
    "EnzymeComplex",
    "EnzymeComplexMember",
    "EnzymeModification",
    "EnzymeState",
    "EnzymeStateTransition",
    "Evidence",
    "EvidenceCondition",
    "ExperimentExecution",
    "ExperimentExecutionEvent",
    "ExperimentRecommendationEvent",
    "ExperimentRecommendationRecord",
    "ExperimentResult",
    "ExperimentalCondition",
    "ExperimentalContext",
    "ExternalRecord",
    "Gene",
    "KineticMeasurement",
    "KineticMeasurementProteinContext",
    "KnowledgeGap",
    "Model",
    "ModelSpecification",
    "ModelSpecificationCompartment",
    "ModelSpecificationEnzymeState",
    "ModelSpecificationGeneProtein",
    "ModelSpecificationPathway",
    "ModelSpecificationReaction",
    "ModelSpecificationRegulatoryInteraction",
    "ModelSpecificationSpecies",
    "ModelingAssumption",
    "ModelingDecision",
    "ModelingDecisionOption",
    "Network",
    "NetworkDisease",
    "NetworkEdge",
    "NetworkNode",
    "NetworkPathway",
    "NetworkPerturbation",
    "NetworkPublication",
    "Organism",
    "Perturbation",
    "Project",
    "ProjectRegulatoryContext",
    "Protein",
    "Publication",
    "QuantitativeObservation",
    "QuantitativeObservationDependency",
    "Reaction",
    "ReactionEnzyme",
    "ReactionParticipant",
    "RegulatoryInteraction",
    "ReviewEvent",
    "SourceCrossReference",
    "Subproject",
]
