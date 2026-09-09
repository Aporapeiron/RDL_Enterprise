"""Framework-independent relation constraint value objects."""

from dataclasses import dataclass
from typing import Optional

from .contracts import AuthorityConstraint, BoundaryContext, EvidencePolarity, Provenance


def _unit_interval(value: float, field_name: str) -> float:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{field_name} は0以上1以下である必要があります: {value}")
    return float(value)


@dataclass(frozen=True)
class ConstraintIdentity:
    """A relation identity, independent of its current activation strength."""

    constraint_id: str
    subject: str
    relation: str
    object: str
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        for field_name in ("constraint_id", "subject", "relation", "object"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} は空にできません")


@dataclass(frozen=True)
class ConstraintStrength:
    """A bounded strength observation, not a truth value."""

    value: float
    support: EvidencePolarity = EvidencePolarity.UNRESOLVED
    relevance: float = 0.0
    freshness: float = 0.0
    authority: float = 0.0

    def __post_init__(self) -> None:
        for field_name in ("value", "relevance", "freshness", "authority"):
            object.__setattr__(self, field_name, _unit_interval(getattr(self, field_name), field_name))


@dataclass(frozen=True)
class ConstraintActivation:
    """A bounded activation of an identity under a recoverable context."""

    identity: ConstraintIdentity
    context: BoundaryContext
    strength: ConstraintStrength
    authority_constraint: Optional[AuthorityConstraint] = None

