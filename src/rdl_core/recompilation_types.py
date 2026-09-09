"""Recompilation requests derived from immutable lifecycle observations."""

from dataclasses import dataclass
from typing import Optional

from .activation_types import ActiveCompiledMB
from .contracts import BoundaryContext, Provenance
from .deactivation_types import DeactivationRecord
from .function_types import FunctionDescription


@dataclass(frozen=True)
class RecompilationRequest:
    """A request to inspect or rebuild a Function; not a compiled result."""

    active: ActiveCompiledMB
    deactivation: DeactivationRecord
    context: BoundaryContext
    evaluator: FunctionDescription
    reason: str = ""
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        if self.deactivation.active != self.active:
            raise ValueError("RecompilationRequestのActive artifactが一致していません")
        if not isinstance(self.reason, str):
            raise TypeError("reasonは文字列である必要があります")


def request_recompilation(
    active: ActiveCompiledMB,
    deactivation: DeactivationRecord,
    context: BoundaryContext,
    *,
    reason: str = "",
    evaluator: FunctionDescription = FunctionDescription("rdl_core.recompilation_policy", "0"),
    provenance: Optional[Provenance] = None,
) -> RecompilationRequest:
    """Create a reinspection request without mutating the active artifact."""
    return RecompilationRequest(
        active, deactivation, context, evaluator, reason=reason, provenance=provenance,
    )
