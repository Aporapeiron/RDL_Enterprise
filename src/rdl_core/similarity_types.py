"""Bounded relation-constraint similarity observations."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .constraint_types import ConstraintIdentity, ConstraintStrength
from .contracts import BoundaryContext, EvidencePolarity, Provenance
from .function_types import FunctionDescription, FunctionInvocation


class SimilarityObservationStatus(str, Enum):
    SIMILAR = "similar"
    NOT_SIMILAR = "not_similar"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class RelationConstraintProfile:
    """A relation identity and its bounded strength observation."""

    identity: ConstraintIdentity
    strength: ConstraintStrength


@dataclass(frozen=True)
class RelationSimilarityObservation:
    """Recoverable similarity result, not a truth or commitment claim."""

    left: RelationConstraintProfile
    right: RelationConstraintProfile
    score: float
    coverage: float
    conflict: float
    status: SimilarityObservationStatus
    context: BoundaryContext
    evaluator: FunctionDescription = FunctionDescription("rdl_core.relation_similarity", "0")
    provenance: Optional[Provenance] = None
    invocation: Optional[FunctionInvocation] = None

    def __post_init__(self) -> None:
        for name, value in (("score", self.score), ("coverage", self.coverage), ("conflict", self.conflict)):
            if not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name}は0以上1以下である必要があります")
        if not isinstance(self.status, SimilarityObservationStatus):
            raise TypeError("statusはSimilarityObservationStatusである必要があります")
        if not isinstance(self.evaluator, FunctionDescription):
            raise TypeError("evaluatorはFunctionDescriptionである必要があります")
        invocation = self.invocation or FunctionInvocation(
            self.evaluator, self.context, provenance=self.provenance
        )
        if invocation.function != self.evaluator or invocation.context != self.context:
            raise ValueError("invocationのFunctionまたはBoundaryが観測記録と一致していません")
        object.__setattr__(self, "invocation", invocation)


@dataclass(frozen=True)
class FunctionEvaluationComparison:
    """Compare outputs only when the finite relation inputs are shared."""

    left: RelationSimilarityObservation
    right: RelationSimilarityObservation

    @property
    def same_inputs(self) -> bool:
        return (
            self.left.left.identity.semantic_key == self.right.left.identity.semantic_key
            and self.left.right.identity.semantic_key == self.right.right.identity.semantic_key
        )

    @property
    def comparable(self) -> bool:
        return (
            self.left.invocation.context == self.right.invocation.context
            and self.left.invocation.purpose == self.right.invocation.purpose
            and self.left.invocation.config == self.right.invocation.config
            and self.same_inputs
        )

    def raw_delta(self) -> float:
        return self.left.score - self.right.score

    def delta(self) -> float:
        if not self.comparable:
            raise ValueError("Function評価のBoundary、設定、目的、入力が一致していません")
        return self.raw_delta()


def compare_relation_constraint_profiles(
    left: RelationConstraintProfile,
    right: RelationConstraintProfile,
    context: BoundaryContext,
    *,
    evaluator: FunctionDescription = FunctionDescription("rdl_core.relation_similarity", "0"),
    provenance: Optional[Provenance] = None,
    invocation: Optional[FunctionInvocation] = None,
) -> RelationSimilarityObservation:
    """Compare two finite relation profiles without inferring truth."""
    same_relation = left.identity.semantic_key == right.identity.semantic_key
    coverage = 1.0 if same_relation else 0.0
    strength_distance = abs(left.strength.value - right.strength.value)
    score = 1.0 - strength_distance if same_relation else 0.0
    conflict = 1.0 if {
        left.strength.support, right.strength.support
    } == {EvidencePolarity.SUPPORT, EvidencePolarity.OPPOSE} else 0.0
    unresolved = (
        left.strength.support == EvidencePolarity.UNRESOLVED
        or right.strength.support == EvidencePolarity.UNRESOLVED
    )
    status = (
        SimilarityObservationStatus.UNRESOLVED if unresolved
        else SimilarityObservationStatus.SIMILAR if score >= 0.5
        else SimilarityObservationStatus.NOT_SIMILAR
    )
    return RelationSimilarityObservation(
        left, right, score, coverage, conflict, status, context,
        evaluator=evaluator, provenance=provenance, invocation=invocation,
    )


def compare_relation_constraint_polarity(
    left: RelationConstraintProfile,
    right: RelationConstraintProfile,
    context: BoundaryContext,
    *,
    provenance: Optional[Provenance] = None,
    invocation: Optional[FunctionInvocation] = None,
) -> RelationSimilarityObservation:
    """Compare polarity independently from numeric constraint strength."""
    evaluator = FunctionDescription("rdl_core.relation_polarity_similarity", "0")
    same_relation = left.identity.semantic_key == right.identity.semantic_key
    coverage = 1.0 if same_relation else 0.0
    unresolved = (
        left.strength.support == EvidencePolarity.UNRESOLVED
        or right.strength.support == EvidencePolarity.UNRESOLVED
    )
    same_polarity = left.strength.support == right.strength.support
    score = 1.0 if same_polarity and same_relation else 0.0
    status = (
        SimilarityObservationStatus.UNRESOLVED if unresolved
        else SimilarityObservationStatus.SIMILAR if score == 1.0
        else SimilarityObservationStatus.NOT_SIMILAR
    )
    return RelationSimilarityObservation(
        left, right, score, coverage, 0.0, status, context,
        evaluator=evaluator, provenance=provenance, invocation=invocation,
    )


def compare_relation_constraint_provenance(
    left: RelationConstraintProfile,
    right: RelationConstraintProfile,
    context: BoundaryContext,
    *,
    provenance: Optional[Provenance] = None,
    invocation: Optional[FunctionInvocation] = None,
) -> RelationSimilarityObservation:
    """Compare relation provenance without treating origin as truth or authority."""
    evaluator = FunctionDescription("rdl_core.relation_provenance_similarity", "0")
    left_source = left.identity.provenance.source if left.identity.provenance else None
    right_source = right.identity.provenance.source if right.identity.provenance else None
    unresolved = left_source is None or right_source is None
    same_relation = left.identity.semantic_key == right.identity.semantic_key
    coverage = 1.0 if same_relation else 0.0
    score = 1.0 if not unresolved and same_relation and left_source == right_source else 0.0
    status = (
        SimilarityObservationStatus.UNRESOLVED if unresolved
        else SimilarityObservationStatus.SIMILAR if score == 1.0
        else SimilarityObservationStatus.NOT_SIMILAR
    )
    return RelationSimilarityObservation(
        left, right, score, coverage, 0.0, status, context,
        evaluator=evaluator, provenance=provenance, invocation=invocation,
    )
