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
    same_identity = left.identity == right.identity
    coverage = 1.0 if same_identity else 0.0
    strength_distance = abs(left.strength.value - right.strength.value)
    score = 1.0 - strength_distance if same_identity else 0.0
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
