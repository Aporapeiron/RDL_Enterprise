"""Explicit activation records after a successful promotion decision."""

from dataclasses import dataclass
from typing import Optional

from .contracts import BoundaryContext, Provenance
from .evolution_types import CompiledMB
from .function_types import FunctionDescription
from .promotion_types import PromotionDecision, PromotionDecisionStatus


@dataclass(frozen=True)
class ActiveCompiledMB:
    """An explicitly registered Compiled M_B; creation requires approval."""

    artifact: CompiledMB
    promotion: PromotionDecision
    registry: FunctionDescription
    context: BoundaryContext
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        if self.promotion.artifact != self.artifact:
            raise ValueError("ActiveCompiledMBのArtifactとPromotionDecisionが一致していません")
        if self.promotion.status != PromotionDecisionStatus.APPROVED:
            raise ValueError("ActiveCompiledMBにはAPPROVEDのPromotionDecisionが必要です")
        if not isinstance(self.registry, FunctionDescription):
            raise TypeError("registryはFunctionDescriptionである必要があります")


def activate_promoted_artifact(
    decision: PromotionDecision,
    context: BoundaryContext,
    *,
    registry: FunctionDescription = FunctionDescription("rdl_core.active_registry", "0"),
    provenance: Optional[Provenance] = None,
) -> ActiveCompiledMB:
    """Register an approved artifact without changing the source decision."""
    return ActiveCompiledMB(
        artifact=decision.artifact,
        promotion=decision,
        registry=registry,
        context=context,
        provenance=provenance or decision.provenance,
    )
