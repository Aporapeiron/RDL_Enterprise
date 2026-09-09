"""Framework-independent relation constraint value objects."""

from dataclasses import dataclass
from typing import Optional

from .contracts import AuthorityConstraint, BoundaryContext, EvidencePolarity, Provenance


def _unit_interval(value: float, field_name: str) -> float:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{field_name} は0以上1以下である必要があります: {value}")
    return float(value)


@dataclass(frozen=True)
class ConstraintEvaluationWeights:
    relevance: float = 0.35
    freshness: float = 0.25
    authority: float = 0.15
    source: float = 0.15
    convergence: float = 0.10

    def __post_init__(self) -> None:
        values = (self.relevance, self.freshness, self.authority, self.source, self.convergence)
        if any(value < 0.0 for value in values):
            raise ValueError("Constraint評価の重みは負にできません")
        if sum(values) <= 0.0:
            raise ValueError("Constraint評価の重み合計は正である必要があります")


@dataclass(frozen=True)
class ConstraintEvaluation:
    """Recoverable record of one bounded constraint evaluation."""

    strength: "ConstraintStrength"
    relevance: float
    freshness: float
    authority: float
    source: float
    convergence: float
    weights: ConstraintEvaluationWeights
    context: BoundaryContext
    evaluator_id: str = "rdl_core.constraint_strength"
    evaluator_version: str = "0"
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        if not isinstance(self.evaluator_id, str) or not isinstance(self.evaluator_version, str):
            raise TypeError("evaluator identity/version は文字列である必要があります")
        if not self.evaluator_id.strip() or not self.evaluator_version.strip():
            raise ValueError("evaluator identity/version は空にできません")


@dataclass(frozen=True)
class ConstraintEvaluationDelta:
    """Difference between two finite evaluators, not error against reality."""

    left_evaluation: ConstraintEvaluation
    right_evaluation: ConstraintEvaluation
    provenance: Optional[Provenance] = None

    @property
    def value(self) -> float:
        return self.left_evaluation.strength.value - self.right_evaluation.strength.value

    @property
    def same_boundary(self) -> bool:
        return self.left_evaluation.context == self.right_evaluation.context


@dataclass(frozen=True)
class ConstraintEvaluationComparison:
    """Comparison eligibility and delta for two finite evaluation records."""

    left: ConstraintEvaluation
    right: ConstraintEvaluation

    @property
    def same_boundary(self) -> bool:
        return self.left.context == self.right.context

    @property
    def same_observations(self) -> bool:
        return (
            self.left.relevance == self.right.relevance
            and self.left.freshness == self.right.freshness
            and self.left.authority == self.right.authority
            and self.left.source == self.right.source
            and self.left.convergence == self.right.convergence
        )

    @property
    def same_evaluator_config(self) -> bool:
        return self.left.weights == self.right.weights

    @property
    def replay_eligible(self) -> bool:
        return self.same_boundary and self.same_observations and self.same_evaluator_config

    @property
    def evaluator_comparison_eligible(self) -> bool:
        return self.same_boundary and self.same_observations

    @property
    def eligible(self) -> bool:
        """Backward-compatible alias for replay eligibility."""
        return self.replay_eligible

    def raw_delta(self, provenance: Optional[Provenance] = None) -> ConstraintEvaluationDelta:
        return ConstraintEvaluationDelta(self.left, self.right, provenance=provenance)

    def delta(self, provenance: Optional[Provenance] = None) -> ConstraintEvaluationDelta:
        if not self.evaluator_comparison_eligible:
            raise ValueError("評価比較のBoundaryまたは観測条件が一致していません")
        return self.raw_delta(provenance=provenance)


def evaluate_constraint_strength(
    *,
    relevance: float,
    freshness: float,
    authority: float,
    source: float,
    convergence: float,
    support: EvidencePolarity = EvidencePolarity.UNRESOLVED,
    weights: ConstraintEvaluationWeights = ConstraintEvaluationWeights(),
) -> "ConstraintStrength":
    """Evaluate bounded relation strength without promoting it to truth.

    The calculation is a pure weighted observation. Polarity is carried
    explicitly and is never inferred from the numeric result.
    """
    components = {
        "relevance": _unit_interval(relevance, "relevance"),
        "freshness": _unit_interval(freshness, "freshness"),
        "authority": _unit_interval(authority, "authority"),
        "source": _unit_interval(source, "source"),
        "convergence": _unit_interval(convergence, "convergence"),
    }
    total_weight = sum((weights.relevance, weights.freshness, weights.authority, weights.source, weights.convergence))
    value = sum(
        getattr(weights, name) * component
        for name, component in components.items()
    ) / total_weight
    return ConstraintStrength(
        value=value,
        support=support,
        relevance=components["relevance"],
        freshness=components["freshness"],
        authority=components["authority"],
    )


def record_constraint_evaluation(
    *,
    context: BoundaryContext,
    relevance: float,
    freshness: float,
    authority: float,
    source: float,
    convergence: float,
    support: EvidencePolarity = EvidencePolarity.UNRESOLVED,
    weights: ConstraintEvaluationWeights = ConstraintEvaluationWeights(),
    evaluator_id: str = "rdl_core.constraint_strength",
    evaluator_version: str = "0",
    provenance: Optional[Provenance] = None,
) -> ConstraintEvaluation:
    strength = evaluate_constraint_strength(
        relevance=relevance, freshness=freshness, authority=authority,
        source=source, convergence=convergence, support=support, weights=weights,
    )
    return ConstraintEvaluation(
        strength=strength,
        relevance=strength.relevance,
        freshness=strength.freshness,
        authority=strength.authority,
        source=_unit_interval(source, "source"),
        convergence=_unit_interval(convergence, "convergence"),
        weights=weights,
        context=context,
        evaluator_id=evaluator_id,
        evaluator_version=evaluator_version,
        provenance=provenance,
    )


@dataclass(frozen=True)
class ConstraintIdentity:
    """A relation identity, independent of activation or Commitment."""

    constraint_id: str
    subject: str
    relation: str
    object: str
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        for field_name in ("constraint_id", "subject", "relation", "object"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} は空にできません")


@dataclass(frozen=True)
class ConstraintStrength:
    """A bounded strength observation, not a truth value or authority proof."""

    value: float
    support: EvidencePolarity = EvidencePolarity.UNRESOLVED
    relevance: float = 0.0
    freshness: float = 0.0
    authority: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.support, EvidencePolarity):
            raise TypeError("support は EvidencePolarity である必要があります")
        for field_name in ("value", "relevance", "freshness", "authority"):
            object.__setattr__(self, field_name, _unit_interval(getattr(self, field_name), field_name))


@dataclass(frozen=True)
class ConstraintActivation:
    """A bounded activation observation under context; not an M_B Commitment."""

    identity: ConstraintIdentity
    context: BoundaryContext
    strength: ConstraintStrength
    authority_constraint: Optional[AuthorityConstraint] = None
