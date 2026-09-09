"""Finite runtime observations for Conditional local M_B execution."""

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from .contracts import BoundaryContext, BoundaryInputValue, FrozenBoundaryValue, Provenance, freeze_boundary_value
from .function_types import FunctionDescription
from .evolution_types import (
    ConditionComposition,
    ConditionObservation,
    ConditionObservationStatus,
    ConditionSet,
    ConditionalCompiledMB,
    ConditionalRelationCandidate,
    ConditionalRuptureRecord,
    ConditionalRuptureStatus,
    evaluate_condition,
)


@dataclass(frozen=True)
class ConditionSetObservation:
    """Condition-level observations plus their bounded aggregate status."""

    condition_set: ConditionSet
    observations: Tuple[ConditionObservation, ...]
    status: ConditionObservationStatus
    context: BoundaryContext
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        observations = tuple(self.observations)
        if any(not isinstance(item, ConditionObservation) for item in observations):
            raise TypeError("observationsはConditionObservationの列である必要があります")
        if not isinstance(self.status, ConditionObservationStatus):
            raise TypeError("statusはConditionObservationStatusである必要があります")
        if self.condition_set.composition != ConditionComposition.AND:
            raise ValueError("v0のConditionSetはANDのみ対応します")
        if len(observations) != len(self.condition_set.conditions):
            raise ValueError("ConditionSetの全Conditionを観測する必要があります")
        if any(item.context != self.context for item in observations):
            raise ValueError("Condition ObservationのBoundaryがConditionSetと一致していません")
        if any(
            not any(item.condition == condition for item in observations)
            for condition in self.condition_set.conditions
        ):
            raise ValueError("Condition Observationの対象がConditionSetと一致していません")
        object.__setattr__(self, "observations", observations)


def evaluate_condition_set(
    condition_set: ConditionSet,
    finite_inputs: Mapping[str, BoundaryInputValue],
    context: BoundaryContext,
    *,
    provenance: Optional[Provenance] = None,
) -> ConditionSetObservation:
    """Evaluate a finite AND set while retaining every condition observation."""
    if not isinstance(condition_set, ConditionSet):
        raise TypeError("condition_setはConditionSetである必要があります")
    observations = tuple(
        evaluate_condition(condition, finite_inputs, context, provenance=provenance)
        for condition in condition_set.conditions
    )
    statuses = tuple(item.status for item in observations)
    if ConditionObservationStatus.NOT_EVALUATED in statuses:
        status = ConditionObservationStatus.NOT_EVALUATED
    elif ConditionObservationStatus.UNRESOLVED in statuses:
        status = ConditionObservationStatus.UNRESOLVED
    elif ConditionObservationStatus.NOT_MATCH in statuses:
        status = ConditionObservationStatus.NOT_MATCH
    else:
        status = ConditionObservationStatus.MATCH
    return ConditionSetObservation(condition_set, observations, status, context, provenance)


@dataclass(frozen=True)
class ConditionalRuntimeObservation:
    """One finite runtime observation of a ConditionalCompiledMB."""

    artifact: ConditionalCompiledMB
    condition_observation: ConditionSetObservation
    input_snapshot: Tuple[Tuple[str, FrozenBoundaryValue], ...]
    context: BoundaryContext
    purpose: str
    evaluator: FunctionDescription
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, ConditionalCompiledMB):
            raise TypeError("artifactはConditionalCompiledMBである必要があります")
        if not isinstance(self.condition_observation, ConditionSetObservation):
            raise TypeError("condition_observationはConditionSetObservationである必要があります")
        if self.condition_observation.context != self.context:
            raise ValueError("Runtime observationのBoundaryがCondition observationと一致していません")
        if self.context != self.artifact.candidate.function_candidate.invocation.context:
            raise ValueError("Runtime observationのBoundaryがCompiled Functionと一致していません")
        if not isinstance(self.purpose, str) or not self.purpose.strip():
            raise ValueError("purposeは空でない文字列である必要があります")
        if not isinstance(self.evaluator, FunctionDescription):
            raise TypeError("evaluatorはFunctionDescriptionである必要があります")
        snapshot = tuple(self.input_snapshot)
        if any(not isinstance(item, tuple) or len(item) != 2 for item in snapshot):
            raise TypeError("input_snapshotは(name, value)の列である必要があります")
        frozen = tuple((name, freeze_boundary_value(value)) for name, value in snapshot)
        object.__setattr__(self, "input_snapshot", frozen)

    @property
    def status(self) -> ConditionObservationStatus:
        return self.condition_observation.status


@dataclass(frozen=True)
class RuntimeMismatchSummary:
    """Finite selected runtime evidence; not an infinite history."""

    candidate: ConditionalRelationCandidate
    observations: Tuple[ConditionalRuntimeObservation, ...]
    context: BoundaryContext
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        observations = tuple(self.observations)
        if any(item.artifact.conditional_candidate != self.candidate for item in observations):
            raise ValueError("Runtime observationのCandidateがSummaryと一致していません")
        if any(item.context != self.context for item in observations):
            raise ValueError("Runtime observationのBoundaryがSummaryと一致していません")
        object.__setattr__(self, "observations", observations)

    def count(self, status: ConditionObservationStatus) -> int:
        return sum(item.status == status for item in self.observations)


@dataclass(frozen=True)
class RupturePolicyDescription:
    """Finite, identifiable policy for converting selected mismatches to rupture evidence."""

    evaluator: FunctionDescription
    required_statuses: Tuple[ConditionObservationStatus, ...]
    threshold: int
    context: BoundaryContext
    purpose: str
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        statuses = tuple(dict.fromkeys(self.required_statuses))
        if not statuses or any(not isinstance(item, ConditionObservationStatus) for item in statuses):
            raise ValueError("required_statusesはConditionObservationStatusの非空列である必要があります")
        if not isinstance(self.threshold, int) or self.threshold < 1:
            raise ValueError("thresholdは1以上の整数である必要があります")
        if not isinstance(self.evaluator, FunctionDescription):
            raise TypeError("evaluatorはFunctionDescriptionである必要があります")
        if not isinstance(self.purpose, str) or not self.purpose.strip():
            raise ValueError("purposeは空でない文字列である必要があります")
        object.__setattr__(self, "required_statuses", statuses)


def evaluate_runtime_rupture(
    summary: RuntimeMismatchSummary,
    policy: RupturePolicyDescription,
    *,
    check_id: str,
    reason: str = "",
) -> ConditionalRuptureRecord:
    """Convert finite runtime evidence to rupture evidence without deactivation."""
    if summary.context != policy.context:
        raise ValueError("Rupture policyのBoundaryがRuntime summaryと一致していません")
    if not isinstance(check_id, str) or not check_id.strip():
        raise ValueError("check_idは空でない文字列である必要があります")
    if not summary.observations:
        status = ConditionalRuptureStatus.NOT_EVALUATED
    elif any(item.status == ConditionObservationStatus.NOT_EVALUATED for item in summary.observations):
        status = ConditionalRuptureStatus.NOT_EVALUATED
    elif sum(item.status in policy.required_statuses for item in summary.observations) >= policy.threshold:
        status = ConditionalRuptureStatus.DETECTED
    elif any(item.status == ConditionObservationStatus.UNRESOLVED for item in summary.observations):
        status = ConditionalRuptureStatus.UNRESOLVED
    else:
        status = ConditionalRuptureStatus.NOT_DETECTED
    return ConditionalRuptureRecord(
        summary.candidate, status, summary.context, check_id, reason, summary.provenance,
    )
