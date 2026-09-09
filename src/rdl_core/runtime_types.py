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
    ConditionalActivationRecord,
    ConditionDescription,
    ExceptionCandidate,
    RelationPatternCandidate,
    RelationSemanticKey,
    build_conditional_relation_candidate,
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
        statuses = tuple(item.status for item in observations)
        if ConditionObservationStatus.NOT_EVALUATED in statuses:
            expected_status = ConditionObservationStatus.NOT_EVALUATED
        elif ConditionObservationStatus.UNRESOLVED in statuses:
            expected_status = ConditionObservationStatus.UNRESOLVED
        elif ConditionObservationStatus.NOT_MATCH in statuses:
            expected_status = ConditionObservationStatus.NOT_MATCH
        else:
            expected_status = ConditionObservationStatus.MATCH
        if self.status != expected_status:
            raise ValueError("ConditionSetのaggregate statusが個別Observationと一致していません")
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
        if self.evaluator != self.artifact.candidate.function_candidate.function:
            raise ValueError("Runtime evaluatorがCompiled Functionと一致していません")
        snapshot = tuple(self.input_snapshot)
        if any(not isinstance(item, tuple) or len(item) != 2 for item in snapshot):
            raise TypeError("input_snapshotは(name, value)の列である必要があります")
        frozen = tuple((name, freeze_boundary_value(value)) for name, value in snapshot)
        object.__setattr__(self, "input_snapshot", frozen)

    @property
    def status(self) -> ConditionObservationStatus:
        return self.condition_observation.status


def record_conditional_runtime_observation(
    artifact: ConditionalCompiledMB,
    condition_set: ConditionSet,
    finite_inputs: Mapping[str, BoundaryInputValue],
    context: BoundaryContext,
    *,
    purpose: str,
    provenance: Optional[Provenance] = None,
) -> ConditionalRuntimeObservation:
    """Create condition observations and their finite snapshot from one input boundary."""
    condition_observation = evaluate_condition_set(
        condition_set, finite_inputs, context, provenance=provenance,
    )
    snapshot = tuple((name, freeze_boundary_value(value)) for name, value in finite_inputs.items())
    return ConditionalRuntimeObservation(
        artifact, condition_observation, snapshot, context, purpose,
        artifact.candidate.function_candidate.function, provenance,
    )


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


@dataclass(frozen=True)
class ConditionalRelearningRequest:
    """Finite request to re-enter Adaptive M_B after observed rupture evidence."""

    active: ConditionalActivationRecord
    ruptures: Tuple[ConditionalRuptureRecord, ...]
    context: BoundaryContext
    reason: str = ""
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        if not isinstance(self.active, ConditionalActivationRecord):
            raise TypeError("activeはConditionalActivationRecordである必要があります")
        ruptures = tuple(self.ruptures)
        if any(not isinstance(item, ConditionalRuptureRecord) for item in ruptures):
            raise TypeError("rupturesはConditionalRuptureRecordの列である必要があります")
        if any(item.candidate != self.active.promotion.artifact.conditional_candidate for item in ruptures):
            raise ValueError("RuptureのCandidateがActive Conditional artifactと一致していません")
        if any(item.context != self.context for item in ruptures):
            raise ValueError("RuptureのBoundaryがRelearning requestと一致していません")
        if not isinstance(self.reason, str):
            raise TypeError("reasonは文字列である必要があります")
        object.__setattr__(self, "ruptures", ruptures)


def request_conditional_relearning(
    active: ConditionalActivationRecord,
    ruptures: Tuple[ConditionalRuptureRecord, ...],
    context: BoundaryContext,
    *,
    reason: str = "runtime rupture",
    provenance: Optional[Provenance] = None,
) -> ConditionalRelearningRequest:
    """Create a re-entry request without deactivating or mutating the active artifact."""
    return ConditionalRelearningRequest(active, ruptures, context, reason, provenance)


def reintroduce_conditional_to_adaptive(
    request: ConditionalRelearningRequest,
    profiles: Tuple[object, ...],
    *,
    provenance: Optional[Provenance] = None,
):
    """Re-enter Adaptive M_B while retaining prior conditional structure and evidence."""
    from .evolution_types import AdaptiveMBProfile
    from .similarity_types import RelationConstraintProfile

    profiles = tuple(profiles)
    if any(not isinstance(item, RelationConstraintProfile) for item in profiles):
        raise TypeError("profilesはRelationConstraintProfileの列である必要があります")
    return AdaptiveMBProfile(
        profiles=profiles,
        context=request.context,
        provenance=provenance or request.provenance,
        prior_structure=request.active.promotion.artifact.generic_artifact.structure,
        recompilation_reason=request.reason,
        relearning_evidence=request.ruptures,
    )


@dataclass(frozen=True)
class ConditionalStructureDelta:
    """Finite change record between conditional candidates, not a quality judgment."""

    previous: ConditionalRelationCandidate
    current: ConditionalRelationCandidate

    def __post_init__(self) -> None:
        if self.previous.context != self.current.context:
            raise ValueError("Conditional structure comparisonのBoundaryが一致していません")

    @property
    def added_conditions(self) -> Tuple[str, ...]:
        return tuple(item for item in self.current.conditions if item not in self.previous.conditions)

    @property
    def removed_conditions(self) -> Tuple[str, ...]:
        return tuple(item for item in self.previous.conditions if item not in self.current.conditions)

    @property
    def added_exceptions(self) -> Tuple[object, ...]:
        return tuple(item for item in self.current.exceptions if item not in self.previous.exceptions)

    @property
    def removed_exceptions(self) -> Tuple[object, ...]:
        return tuple(item for item in self.previous.exceptions if item not in self.current.exceptions)

    @property
    def added_structured_conditions(self) -> Tuple[ConditionDescription, ...]:
        return tuple(item for item in self.current.structured_conditions
                     if item not in self.previous.structured_conditions)

    @property
    def removed_structured_conditions(self) -> Tuple[ConditionDescription, ...]:
        return tuple(item for item in self.previous.structured_conditions
                     if item not in self.current.structured_conditions)

    @property
    def added_exception_candidates(self) -> Tuple[ExceptionCandidate, ...]:
        return tuple(item for item in self.current.exception_candidates
                     if item not in self.previous.exception_candidates)

    @property
    def removed_exception_candidates(self) -> Tuple[ExceptionCandidate, ...]:
        return tuple(item for item in self.previous.exception_candidates
                     if item not in self.current.exception_candidates)

    @property
    def changed_structured_conditions(self) -> Tuple[Tuple[ConditionDescription, ConditionDescription], ...]:
        previous_by_id = {item.condition_id: item for item in self.previous.structured_conditions}
        return tuple(
            (previous_by_id[item.condition_id], item)
            for item in self.current.structured_conditions
            if item.condition_id in previous_by_id and previous_by_id[item.condition_id] != item
        )


@dataclass(frozen=True)
class ConditionalSupersessionRecord:
    """Conditional v1 to vNext supersession lineage over generic active artifacts."""

    predecessor: ConditionalActivationRecord
    replacement: ConditionalActivationRecord
    request: ConditionalRelearningRequest
    structure_delta: ConditionalStructureDelta
    context: BoundaryContext
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        if self.request.active != self.predecessor:
            raise ValueError("Relearning requestのActiveがSupersession predecessorと一致していません")
        if self.predecessor.active.artifact == self.replacement.active.artifact:
            raise ValueError("同一artifactをSupersession replacementにはできません")
        if self.structure_delta.previous != self.predecessor.promotion.artifact.conditional_candidate:
            raise ValueError("StructureDeltaのpreviousがpredecessorと一致していません")
        if self.structure_delta.current != self.replacement.promotion.artifact.conditional_candidate:
            raise ValueError("StructureDeltaのcurrentがreplacementと一致していません")
        if self.context != self.request.context:
            raise ValueError("SupersessionのBoundaryがRelearning requestと一致していません")


def record_conditional_supersession(
    predecessor: ConditionalActivationRecord,
    replacement: ConditionalActivationRecord,
    request: ConditionalRelearningRequest,
    delta: ConditionalStructureDelta,
    context: BoundaryContext,
    *,
    provenance: Optional[Provenance] = None,
) -> ConditionalSupersessionRecord:
    """Record v1 to vNext replacement without deleting predecessor history."""
    return ConditionalSupersessionRecord(
        predecessor, replacement, request, delta, context, provenance,
    )


def build_conditional_vnext(
    request: ConditionalRelearningRequest,
    pattern: RelationPatternCandidate,
    *,
    conditions: Optional[Tuple[str, ...]] = None,
    structured_conditions: Optional[Tuple[ConditionDescription, ...]] = None,
    exceptions: Optional[Tuple[RelationSemanticKey, ...]] = None,
    exception_candidates: Optional[Tuple[ExceptionCandidate, ...]] = None,
    provenance: Optional[Provenance] = None,
) -> Tuple[ConditionalRelationCandidate, ConditionalStructureDelta]:
    """Build a vNext candidate from prior lineage plus explicitly supplied new structure."""
    previous = request.active.promotion.artifact.conditional_candidate
    current = build_conditional_relation_candidate(
        pattern,
        conditions=previous.conditions if conditions is None else conditions,
        structured_conditions=(previous.structured_conditions
                               if structured_conditions is None else structured_conditions),
        exceptions=previous.exceptions if exceptions is None else exceptions,
        exception_candidates=(previous.exception_candidates
                              if exception_candidates is None else exception_candidates),
        context=request.context,
        provenance=provenance or request.provenance or previous.provenance,
    )
    return current, ConditionalStructureDelta(previous, current)
