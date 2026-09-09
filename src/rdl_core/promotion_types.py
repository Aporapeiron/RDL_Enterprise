"""Explicit promotion decisions for validated Compiled M_B artifacts."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from .contracts import BoundaryContext, Provenance
from .evolution_types import CompiledMB
from .function_types import FunctionDescription
from .rupture_types import RuptureObservation, RuptureObservationStatus


class PromotionDecisionStatus(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    UNRESOLVED = "unresolved"
    NOT_EVALUATED = "not_evaluated"


@dataclass(frozen=True)
class PromotionDecision:
    """Decision record; approval does not mutate or activate the artifact."""

    artifact: CompiledMB
    status: PromotionDecisionStatus
    policy: FunctionDescription
    context: BoundaryContext
    ruptures: Tuple[RuptureObservation, ...] = ()
    required_checks: Tuple[str, ...] = ()
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, PromotionDecisionStatus):
            raise TypeError("statusはPromotionDecisionStatusである必要があります")
        if not isinstance(self.policy, FunctionDescription):
            raise TypeError("policyはFunctionDescriptionである必要があります")
        ruptures = tuple(self.ruptures)
        if any(not isinstance(item, RuptureObservation) for item in ruptures):
            raise TypeError("rupturesはRuptureObservationの列である必要があります")
        object.__setattr__(self, "ruptures", ruptures)
        required = tuple(self.required_checks)
        if any(not isinstance(item, str) or not item.strip() for item in required):
            raise ValueError("required_checksは非空文字列の列である必要があります")
        object.__setattr__(self, "required_checks", required)


def evaluate_promotion(
    artifact: CompiledMB,
    context: BoundaryContext,
    *,
    ruptures: Tuple[RuptureObservation, ...] = (),
    required_checks: Tuple[str, ...] = (),
    policy: FunctionDescription = FunctionDescription("rdl_core.promotion_policy", "0"),
    provenance: Optional[Provenance] = None,
) -> PromotionDecision:
    """Evaluate a promotion gate without activating the Compiled M_B."""
    observations = tuple(ruptures)
    candidate = artifact.validation.candidate
    if any(item.candidate != candidate for item in observations):
        raise ValueError("RuptureObservationがPromotion対象のFunctionCandidateと一致していません")
    required = tuple(required_checks)
    observed_checks = {item.check_id for item in observations}
    if not observations or any(check not in observed_checks for check in required):
        status = PromotionDecisionStatus.NOT_EVALUATED
    elif any(item.status == RuptureObservationStatus.DETECTED for item in observations):
        status = PromotionDecisionStatus.REJECTED
    elif any(item.status in (RuptureObservationStatus.UNRESOLVED, RuptureObservationStatus.NOT_EVALUATED)
             for item in observations):
        status = PromotionDecisionStatus.UNRESOLVED
    else:
        status = PromotionDecisionStatus.APPROVED
    return PromotionDecision(
        artifact, status, policy, context, observations,
        required_checks=required, provenance=provenance,
    )
