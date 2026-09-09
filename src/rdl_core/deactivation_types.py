"""Explicit deactivation records for active Compiled M_B artifacts."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .activation_types import ActiveCompiledMB
from .contracts import BoundaryContext, Provenance
from .function_types import FunctionDescription


class DeactivationStatus(str, Enum):
    DEACTIVATED = "deactivated"
    UNRESOLVED = "unresolved"
    NOT_EVALUATED = "not_evaluated"


@dataclass(frozen=True)
class DeactivationRecord:
    """A bounded lifecycle record; it does not erase prior activation history."""

    active: ActiveCompiledMB
    status: DeactivationStatus
    context: BoundaryContext
    evaluator: FunctionDescription
    reason: str = ""
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, DeactivationStatus):
            raise TypeError("statusはDeactivationStatusである必要があります")
        if not isinstance(self.reason, str):
            raise TypeError("reasonは文字列である必要があります")


def record_deactivation(
    active: ActiveCompiledMB,
    status: DeactivationStatus,
    context: BoundaryContext,
    *,
    reason: str = "",
    evaluator: FunctionDescription = FunctionDescription("rdl_core.deactivation_policy", "0"),
    provenance: Optional[Provenance] = None,
) -> DeactivationRecord:
    """Record a lifecycle decision without mutating the active artifact."""
    return DeactivationRecord(
        active, status, context, evaluator, reason=reason, provenance=provenance,
    )
