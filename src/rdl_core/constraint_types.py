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
