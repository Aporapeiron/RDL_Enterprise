"""Bounded rupture observations for Function candidates."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .contracts import BoundaryContext, Provenance
from .evolution_types import FunctionCandidate
from .function_types import FunctionDescription


class RuptureObservationStatus(str, Enum):
    DETECTED = "detected"
    NOT_DETECTED = "not_detected"
    UNRESOLVED = "unresolved"
    NOT_EVALUATED = "not_evaluated"


@dataclass(frozen=True)
class RuptureObservation:
    """A bounded compatibility observation, not a rejection of truth."""

    candidate: FunctionCandidate
    status: RuptureObservationStatus
    context: BoundaryContext
    evaluator: FunctionDescription
    reason: str = ""
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, RuptureObservationStatus):
            raise TypeError("statusはRuptureObservationStatusである必要があります")
        if not isinstance(self.evaluator, FunctionDescription):
            raise TypeError("evaluatorはFunctionDescriptionである必要があります")
        if not isinstance(self.reason, str):
            raise TypeError("reasonは文字列である必要があります")


def record_rupture_observation(
    candidate: FunctionCandidate,
    status: RuptureObservationStatus,
    context: BoundaryContext,
    *,
    reason: str = "",
    evaluator: FunctionDescription = FunctionDescription("rdl_core.rupture_check", "0"),
    provenance: Optional[Provenance] = None,
) -> RuptureObservation:
    """Record rupture inspection without changing candidate state."""
    return RuptureObservation(
        candidate, status, context, evaluator, reason=reason, provenance=provenance,
    )
