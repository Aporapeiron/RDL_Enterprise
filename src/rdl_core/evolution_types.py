"""Bounded types for adaptive structure and Function compilation candidates."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from .constraint_types import RelationSemanticKey
from .contracts import BoundaryContext, EvidencePolarity, Provenance
from .function_types import FunctionDescription, FunctionInvocation
from .similarity_types import (
    RelationConstraintProfile,
    RelationSimilarityObservation,
    SimilarityObservationStatus,
    RelationSemanticSimilarityObservation,
)


class SimilarityMetric(str, Enum):
    STRENGTH = "strength"
    POLARITY = "polarity"
    PROVENANCE = "provenance"


class CompilationValidationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNRESOLVED = "unresolved"
    NOT_EVALUATED = "not_evaluated"


class ConditionalValidationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNRESOLVED = "unresolved"
    NOT_EVALUATED = "not_evaluated"


class ConditionalRuptureStatus(str, Enum):
    DETECTED = "detected"
    NOT_DETECTED = "not_detected"
    UNRESOLVED = "unresolved"
    NOT_EVALUATED = "not_evaluated"


class PatternSlotKind(str, Enum):
    FIXED = "fixed"
    VARIABLE = "variable"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class PatternEvidence:
    """Finite evidence summary; not confidence, truth, or commitment."""

    member_count: int
    cohesion: float
    conflicting_edge_count: int
    unresolved_edge_count: int
    specificity: float

    def __post_init__(self) -> None:
        if not isinstance(self.member_count, int) or self.member_count < 1:
            raise ValueError("member_countは1以上の整数である必要があります")
        for name, value in (("cohesion", self.cohesion), ("specificity", self.specificity)):
            if not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name}は0以上1以下である必要があります")
        if (not isinstance(self.conflicting_edge_count, int)
                or not isinstance(self.unresolved_edge_count, int)
                or self.conflicting_edge_count < 0
                or self.unresolved_edge_count < 0):
            raise ValueError("edge countは0以上である必要があります")

    @property
    def edge_count(self) -> int:
        return self.conflicting_edge_count + self.unresolved_edge_count

    @property
    def conflict_ratio(self) -> float:
        return self.conflicting_edge_count / max(1, self.member_count)

    @property
    def unresolved_ratio(self) -> float:
        return self.unresolved_edge_count / max(1, self.member_count)


@dataclass(frozen=True)
class PatternSlotEvidence:
    """Evidence localized to one relation slot."""

    slot: str
    kind: PatternSlotKind
    observed_values: Tuple[str, ...]
    support_count: int = 0
    conflict_count: int = 0
    unresolved_count: int = 0

    def __post_init__(self) -> None:
        if self.slot not in ("subject", "relation", "object"):
            raise ValueError("slotはsubject/relation/objectのいずれかである必要があります")
        if not isinstance(self.kind, PatternSlotKind):
            raise TypeError("kindはPatternSlotKindである必要があります")
        object.__setattr__(self, "observed_values", tuple(dict.fromkeys(self.observed_values)))
        for name in ("support_count", "conflict_count", "unresolved_count"):
            value = getattr(self, name)
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"{name}は0以上の整数である必要があります")


@dataclass(frozen=True)
class PatternVariableBinding:
    """Observed finite values for one variable relation slot."""

    slot: str
    values: Tuple[str, ...]

    def __post_init__(self) -> None:
        if self.slot not in ("subject", "relation", "object"):
            raise ValueError("slotはsubject/relation/objectのいずれかである必要があります")
        values = tuple(dict.fromkeys(self.values))
        if not values or any(not isinstance(value, str) or not value for value in values):
            raise ValueError("valuesは空でない文字列の列である必要があります")
        object.__setattr__(self, "values", values)


@dataclass(frozen=True)
class SimilarityVector:
    """Metric-separated similarity values; dimensions are not implicitly aggregated."""

    strength: Optional[float] = None
    polarity: Optional[float] = None
    provenance: Optional[float] = None

    def __post_init__(self) -> None:
        for name in ("strength", "polarity", "provenance"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0):
                raise ValueError(f"{name} similarityは0以上1以下である必要があります")


@dataclass(frozen=True)
class AdaptiveMBProfile:
    """Finite observed relation profiles eligible for structure inspection."""

    profiles: Tuple[RelationConstraintProfile, ...]
    context: BoundaryContext
    provenance: Optional[Provenance] = None
    prior_structure: Optional["StructureCandidate"] = None
    recompilation_reason: str = ""

    def __post_init__(self) -> None:
        profiles = tuple(self.profiles)
        if any(not isinstance(profile, RelationConstraintProfile) for profile in profiles):
            raise TypeError("profilesはRelationConstraintProfileの列である必要があります")
        if self.prior_structure is not None and not isinstance(self.prior_structure, StructureCandidate):
            raise TypeError("prior_structureはStructureCandidateである必要があります")
        if not isinstance(self.recompilation_reason, str):
            raise TypeError("recompilation_reasonは文字列である必要があります")
        object.__setattr__(self, "profiles", profiles)


@dataclass(frozen=True)
class StructureCandidate:
    """Candidate relation structure; creation does not imply Commitment."""

    relations: Tuple[RelationSemanticKey, ...]
    context: BoundaryContext
    provenance: Optional[Provenance] = None
    supporting_profiles: Tuple[RelationConstraintProfile, ...] = ()
    conflicting_profiles: Tuple[RelationConstraintProfile, ...] = ()
    unresolved_count: int = 0
    similarity: Optional["SimilarityVector"] = None
    conditions: Tuple[str, ...] = ()
    exceptions: Tuple[RelationSemanticKey, ...] = ()

    def __post_init__(self) -> None:
        relations = tuple(self.relations)
        if any(not isinstance(relation, RelationSemanticKey) for relation in relations):
            raise TypeError("relationsはRelationSemanticKeyの列である必要があります")
        for name in ("supporting_profiles", "conflicting_profiles"):
            profiles = tuple(getattr(self, name))
            if any(not isinstance(profile, RelationConstraintProfile) for profile in profiles):
                raise TypeError(f"{name}はRelationConstraintProfileの列である必要があります")
            object.__setattr__(self, name, profiles)
        if not isinstance(self.unresolved_count, int) or self.unresolved_count < 0:
            raise ValueError("unresolved_countは0以上の整数である必要があります")
        object.__setattr__(self, "relations", relations)
        conditions = tuple(self.conditions)
        if any(not isinstance(item, str) or not item.strip() for item in conditions):
            raise ValueError("conditionsは空でない文字列の列である必要があります")
        exceptions = tuple(dict.fromkeys(self.exceptions))
        if any(not isinstance(item, RelationSemanticKey) for item in exceptions):
            raise TypeError("exceptionsはRelationSemanticKeyの列である必要があります")
        object.__setattr__(self, "conditions", conditions)
        object.__setattr__(self, "exceptions", exceptions)


@dataclass(frozen=True)
class StructureInductionResult:
    """Similarity-guided structure evidence without automatic commitment."""

    candidate: StructureCandidate
    common_relations: Tuple[RelationSemanticKey, ...]
    exception_relations: Tuple[RelationSemanticKey, ...]
    unresolved_observations: Tuple[RelationSimilarityObservation, ...] = ()
    unexamined_relations: Tuple[RelationSemanticKey, ...] = ()
    cluster_candidates: Tuple["RelationClusterCandidate", ...] = ()

    def __post_init__(self) -> None:
        for name in ("common_relations", "exception_relations"):
            values = tuple(getattr(self, name))
            if any(not isinstance(value, RelationSemanticKey) for value in values):
                raise TypeError(f"{name}はRelationSemanticKeyの列である必要があります")
            object.__setattr__(self, name, values)
        observations = tuple(self.unresolved_observations)
        if any(not isinstance(item, RelationSimilarityObservation) for item in observations):
            raise TypeError("unresolved_observationsはRelationSimilarityObservationの列である必要があります")
        object.__setattr__(self, "unresolved_observations", observations)
        unexamined = tuple(self.unexamined_relations)
        if any(not isinstance(value, RelationSemanticKey) for value in unexamined):
            raise TypeError("unexamined_relationsはRelationSemanticKeyの列である必要があります")
        object.__setattr__(self, "unexamined_relations", unexamined)
        clusters = tuple(self.cluster_candidates)
        if any(not isinstance(value, RelationClusterCandidate) for value in clusters):
            raise TypeError("cluster_candidatesはRelationClusterCandidateの列である必要があります")
        object.__setattr__(self, "cluster_candidates", clusters)


@dataclass(frozen=True)
class RelationClusterCandidate:
    """A similarity-derived relation cluster candidate, not a semantic commitment."""

    members: Tuple[RelationSemanticKey, ...]
    observations: Tuple[RelationSemanticSimilarityObservation, ...]
    context: BoundaryContext
    provenance: Optional[Provenance] = None
    conflicting_edges: Tuple[RelationSemanticSimilarityObservation, ...] = ()
    unresolved_edges: Tuple[RelationSemanticSimilarityObservation, ...] = ()

    def __post_init__(self) -> None:
        members = tuple(dict.fromkeys(self.members))
        if any(not isinstance(item, RelationSemanticKey) for item in members):
            raise TypeError("membersはRelationSemanticKeyの列である必要があります")
        observations = tuple(self.observations)
        if any(not isinstance(item, RelationSemanticSimilarityObservation) for item in observations):
            raise TypeError("observationsはRelationSemanticSimilarityObservationの列である必要があります")
        object.__setattr__(self, "members", members)
        object.__setattr__(self, "observations", observations)
        for name in ("conflicting_edges", "unresolved_edges"):
            edges = tuple(getattr(self, name))
            if any(not isinstance(item, RelationSemanticSimilarityObservation) for item in edges):
                raise TypeError(f"{name}はRelationSemanticSimilarityObservationの列である必要があります")
            object.__setattr__(self, name, edges)

    @property
    def cohesion(self) -> float:
        """Mean observed similarity; connectedness is not mutual similarity."""
        if not self.observations:
            return 0.0
        return sum(item.score for item in self.observations) / len(self.observations)

    @property
    def support_cohesion(self) -> float:
        supported = tuple(
            item for item in self.observations
            if item.status == SimilarityObservationStatus.SIMILAR
        )
        if not supported:
            return 0.0
        return sum(item.score for item in supported) / len(supported)

    @property
    def coverage(self) -> float:
        possible = len(self.members) * (len(self.members) - 1) / 2
        return min(1.0, len(self.observations) / possible) if possible else 0.0

    @property
    def connected(self) -> bool:
        return len(self.members) > 1


@dataclass(frozen=True)
class RelationPatternCandidate:
    """Common relation-position pattern derived from a cluster, not a rule."""

    cluster: RelationClusterCandidate
    subject: Optional[str]
    relation: Optional[str]
    object: Optional[str]

    @property
    def slot_kinds(self) -> Tuple[PatternSlotKind, ...]:
        return tuple(
            PatternSlotKind.FIXED if value is not None
            else PatternSlotKind.UNRESOLVED if self.cluster.unresolved_edges
            else PatternSlotKind.VARIABLE
            for value in (self.subject, self.relation, self.object)
        )

    @property
    def fully_specified(self) -> bool:
        return self.subject is not None and self.relation is not None and self.object is not None

    @property
    def varying_slots(self) -> Tuple[str, ...]:
        return tuple(
            name for name, value in (
                ("subject", self.subject),
                ("relation", self.relation),
                ("object", self.object),
            ) if value is None
        )

    @property
    def specificity(self) -> float:
        return 1.0 - (len(self.varying_slots) / 3.0)

    @property
    def evidence(self) -> PatternEvidence:
        return PatternEvidence(
            member_count=len(self.cluster.members),
            cohesion=self.cluster.cohesion,
            conflicting_edge_count=len(self.cluster.conflicting_edges),
            unresolved_edge_count=len(self.cluster.unresolved_edges),
            specificity=self.specificity,
        )

    @property
    def slot_evidence(self) -> Tuple[PatternSlotEvidence, ...]:
        result = []
        for slot in ("subject", "relation", "object"):
            values = tuple(dict.fromkeys(getattr(member, slot) for member in self.cluster.members))
            unresolved = sum(
                getattr(edge.left, slot) != getattr(edge.right, slot)
                for edge in self.cluster.unresolved_edges
            )
            conflict = sum(
                getattr(edge.left, slot) != getattr(edge.right, slot)
                for edge in self.cluster.conflicting_edges
            )
            if len(values) == 1:
                kind = PatternSlotKind.FIXED
            elif unresolved:
                kind = PatternSlotKind.UNRESOLVED
            else:
                kind = PatternSlotKind.VARIABLE
            result.append(PatternSlotEvidence(
                slot=slot, kind=kind, observed_values=values,
                support_count=max(0, len(self.cluster.observations) - conflict - unresolved),
                conflict_count=conflict, unresolved_count=unresolved,
            ))
        return tuple(result)


@dataclass(frozen=True)
class ConditionalRelationCandidate:
    """Finite conditional relation candidate; creation does not imply activation."""

    pattern: RelationPatternCandidate
    conditions: Tuple[str, ...] = ()
    exceptions: Tuple[RelationSemanticKey, ...] = ()
    unresolved_slots: Tuple[PatternSlotEvidence, ...] = ()
    evidence: Optional[PatternEvidence] = None
    context: Optional[BoundaryContext] = None
    provenance: Optional[Provenance] = None
    variable_bindings: Tuple[PatternVariableBinding, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.pattern, RelationPatternCandidate):
            raise TypeError("patternはRelationPatternCandidateである必要があります")
        conditions = tuple(self.conditions)
        if any(not isinstance(item, str) or not item.strip() for item in conditions):
            raise ValueError("conditionsは空でない文字列の列である必要があります")
        exceptions = tuple(dict.fromkeys(self.exceptions))
        if any(not isinstance(item, RelationSemanticKey) for item in exceptions):
            raise TypeError("exceptionsはRelationSemanticKeyの列である必要があります")
        unresolved = tuple(self.unresolved_slots) or tuple(
            item for item in self.pattern.slot_evidence if item.kind == PatternSlotKind.UNRESOLVED
        )
        if any(not isinstance(item, PatternSlotEvidence) for item in unresolved):
            raise TypeError("unresolved_slotsはPatternSlotEvidenceの列である必要があります")
        if self.evidence is not None and not isinstance(self.evidence, PatternEvidence):
            raise TypeError("evidenceはPatternEvidenceである必要があります")
        bindings = tuple(self.variable_bindings)
        if any(not isinstance(item, PatternVariableBinding) for item in bindings):
            raise TypeError("variable_bindingsはPatternVariableBindingの列である必要があります")
        object.__setattr__(self, "conditions", conditions)
        object.__setattr__(self, "exceptions", exceptions)
        object.__setattr__(self, "unresolved_slots", unresolved)
        object.__setattr__(self, "variable_bindings", bindings)

    @property
    def variable_slots(self) -> Tuple[str, ...]:
        return tuple(
            item.slot for item in self.pattern.slot_evidence
            if item.kind == PatternSlotKind.VARIABLE
        )

    @property
    def validation_blockers(self) -> Tuple[str, ...]:
        blockers = []
        if self.context is None:
            blockers.append("missing_context")
        if self.unresolved_slots:
            blockers.append("unresolved_slots")
        if not self.conditions:
            blockers.append("missing_conditions")
        if self.evidence is None or self.evidence.member_count < 1:
            blockers.append("missing_evidence")
        return tuple(blockers)

    @property
    def eligible_for_validation(self) -> bool:
        return not self.validation_blockers


def build_conditional_relation_candidate(
    pattern: RelationPatternCandidate,
    *,
    conditions: Tuple[str, ...] = (),
    exceptions: Tuple[RelationSemanticKey, ...] = (),
    context: Optional[BoundaryContext] = None,
    provenance: Optional[Provenance] = None,
) -> ConditionalRelationCandidate:
    """Package an inspected pattern as a conditional candidate without promotion."""
    inferred_exceptions = tuple(dict.fromkeys(
        edge.left for edge in pattern.cluster.conflicting_edges
    ))
    return ConditionalRelationCandidate(
        pattern=pattern,
        conditions=conditions,
        exceptions=exceptions or inferred_exceptions,
        evidence=pattern.evidence,
        context=context or pattern.cluster.context,
        provenance=provenance or pattern.cluster.provenance,
        variable_bindings=tuple(
            PatternVariableBinding(item.slot, item.observed_values)
            for item in pattern.slot_evidence
            if item.kind == PatternSlotKind.VARIABLE and item.observed_values
        ),
    )


@dataclass(frozen=True)
class ConditionalValidationRecord:
    """Finite validation observation for a conditional candidate."""

    candidate: ConditionalRelationCandidate
    status: ConditionalValidationStatus
    context: BoundaryContext
    reason: str = ""
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, ConditionalRelationCandidate):
            raise TypeError("candidateはConditionalRelationCandidateである必要があります")
        if not isinstance(self.status, ConditionalValidationStatus):
            raise TypeError("statusはConditionalValidationStatusである必要があります")
        if not isinstance(self.reason, str):
            raise TypeError("reasonは文字列である必要があります")
        if self.status == ConditionalValidationStatus.PASSED and not self.candidate.eligible_for_validation:
            raise ValueError("阻害要因のある候補をPASSEDにはできません")


def record_conditional_validation(
    candidate: ConditionalRelationCandidate,
    status: ConditionalValidationStatus,
    context: BoundaryContext,
    *,
    reason: str = "",
    provenance: Optional[Provenance] = None,
) -> ConditionalValidationRecord:
    """Record validation without promoting the candidate to a Function or Commitment."""
    return ConditionalValidationRecord(candidate, status, context, reason, provenance)


def compile_conditional_function_candidate(
    validation: ConditionalValidationRecord,
    function: FunctionDescription,
    *,
    purpose: str,
    config: Optional[dict] = None,
) -> "FunctionCandidate":
    """Build a FunctionCandidate from passed conditional validation, without activation."""
    if validation.status != ConditionalValidationStatus.PASSED:
        raise ValueError("条件候補にはPASSEDのValidationRecordが必要です")
    candidate = validation.candidate
    structure = StructureCandidate(
        relations=tuple(member for member in candidate.pattern.cluster.members),
        context=candidate.context or validation.context,
        provenance=candidate.provenance,
        conditions=candidate.conditions,
        exceptions=candidate.exceptions,
    )
    invocation = FunctionInvocation(
        function, structure.context, purpose=purpose, config=config or {},
        provenance=candidate.provenance,
    )
    return FunctionCandidate(function, invocation, structure)


def compile_conditionally_verified_function_candidate(
    validation: ConditionalValidationRecord,
    ruptures: Tuple["ConditionalRuptureRecord", ...],
    function: FunctionDescription,
    *,
    purpose: str,
    config: Optional[dict] = None,
    required_checks: Tuple[str, ...] = (),
) -> "FunctionCandidate":
    """Build a FunctionCandidate only after explicit candidate-matched rupture checks."""
    if validation.status != ConditionalValidationStatus.PASSED:
        raise ValueError("条件候補にはPASSEDのValidationRecordが必要です")
    records = tuple(ruptures)
    coverage = inspect_conditional_rupture_coverage(records, required_checks=required_checks)
    if any(item.candidate != validation.candidate for item in records):
        raise ValueError("RuptureRecordのCandidateがValidation対象と一致していません")
    if not coverage.complete:
        raise ValueError("条件候補のRuptureが未解決または検出済みです")
    return compile_conditional_function_candidate(
        validation, function, purpose=purpose, config=config,
    )


def record_conditional_compilation_validation(
    validation: ConditionalValidationRecord,
    ruptures: Tuple["ConditionalRuptureRecord", ...],
    function: FunctionDescription,
    *,
    purpose: str,
    validation_context: BoundaryContext,
    config: Optional[dict] = None,
    required_checks: Tuple[str, ...] = (),
) -> "CompilationRecord":
    """Record compilation validation for a conditionally verified candidate."""
    candidate = compile_conditionally_verified_function_candidate(
        validation, ruptures, function,
        purpose=purpose, config=config, required_checks=required_checks,
    )
    return record_compilation_validation(
        candidate, CompilationValidationStatus.PASSED, validation_context,
        provenance=validation.provenance,
    )


def materialize_conditional_compiled_mb(
    validation: "CompilationRecord",
) -> "CompiledMB":
    """Materialize a CompiledMB only from a passed conditional compilation record."""
    if not isinstance(validation, CompilationRecord):
        raise TypeError("validationはCompilationRecordである必要があります")
    return compile_validated_candidate(validation)


def translate_conditional_ruptures_to_function(
    function_candidate: "FunctionCandidate",
    conditional_candidate: ConditionalRelationCandidate,
    records: Tuple["ConditionalRuptureRecord", ...],
) -> Tuple[object, ...]:
    """Translate explicit conditional rupture evidence for an existing FunctionCandidate."""
    from .rupture_types import RuptureObservationStatus, record_rupture_observation

    records = tuple(records)
    if any(not isinstance(item, ConditionalRuptureRecord) for item in records):
        raise TypeError("recordsはConditionalRuptureRecordの列である必要があります")
    if any(item.candidate != conditional_candidate for item in records):
        raise ValueError("Conditional RuptureのCandidateが指定対象と一致していません")
    status_map = {
        ConditionalRuptureStatus.DETECTED: RuptureObservationStatus.DETECTED,
        ConditionalRuptureStatus.NOT_DETECTED: RuptureObservationStatus.NOT_DETECTED,
        ConditionalRuptureStatus.UNRESOLVED: RuptureObservationStatus.UNRESOLVED,
        ConditionalRuptureStatus.NOT_EVALUATED: RuptureObservationStatus.NOT_EVALUATED,
    }
    return tuple(
        record_rupture_observation(
            function_candidate,
            status_map[item.status],
            item.context,
            evaluator=function_candidate.function,
            check_id=item.check_id,
            reason=item.reason,
            provenance=item.provenance,
        )
        for item in records
    )


def evaluate_conditional_promotion(
    artifact: "CompiledMB",
    function_candidate: "FunctionCandidate",
    conditional_candidate: ConditionalRelationCandidate,
    ruptures: Tuple["ConditionalRuptureRecord", ...],
    context: BoundaryContext,
    *,
    required_checks: Tuple[str, ...] = (),
    policy_description: Optional[object] = None,
    policy: Optional[FunctionDescription] = None,
    provenance: Optional[Provenance] = None,
) -> object:
    """Evaluate conditional evidence through the existing Promotion Gate."""
    from .promotion_types import evaluate_promotion

    translated = translate_conditional_ruptures_to_function(
        function_candidate, conditional_candidate, ruptures,
    )
    return evaluate_promotion(
        artifact, context, ruptures=translated, required_checks=required_checks,
        policy_description=policy_description,
        policy=policy or FunctionDescription("rdl_core.promotion_policy", "0"),
        provenance=provenance,
    )


def activate_conditional_promotion(
    decision: object,
    context: BoundaryContext,
    *,
    registry: Optional[FunctionDescription] = None,
    provenance: Optional[Provenance] = None,
) -> object:
    """Activate an already approved conditional promotion decision explicitly."""
    from .activation_types import activate_promoted_artifact
    from .promotion_types import PromotionDecision, PromotionDecisionStatus

    if not isinstance(decision, PromotionDecision):
        raise TypeError("decisionはPromotionDecisionである必要があります")
    if decision.status != PromotionDecisionStatus.APPROVED:
        raise ValueError("条件付きActivationにはAPPROVEDのPromotionDecisionが必要です")
    kwargs = {"provenance": provenance}
    if registry is not None:
        kwargs["registry"] = registry
    return activate_promoted_artifact(decision, context, **kwargs)


@dataclass(frozen=True)
class ConditionalRuptureRecord:
    """Finite rupture observation for a conditional candidate."""

    candidate: ConditionalRelationCandidate
    status: ConditionalRuptureStatus
    context: BoundaryContext
    check_id: str
    reason: str = ""
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, ConditionalRelationCandidate):
            raise TypeError("candidateはConditionalRelationCandidateである必要があります")
        if not isinstance(self.status, ConditionalRuptureStatus):
            raise TypeError("statusはConditionalRuptureStatusである必要があります")
        if not isinstance(self.check_id, str) or not self.check_id.strip():
            raise ValueError("check_idは空でない文字列である必要があります")
        if not isinstance(self.reason, str):
            raise TypeError("reasonは文字列である必要があります")


@dataclass(frozen=True)
class ConditionalRuptureCoverage:
    records: Tuple[ConditionalRuptureRecord, ...]
    required_checks: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        records = tuple(self.records)
        if any(not isinstance(item, ConditionalRuptureRecord) for item in records):
            raise TypeError("recordsはConditionalRuptureRecordの列である必要があります")
        if len({item.check_id for item in records}) != len(records):
            raise ValueError("RuptureRecordのcheck_idが重複しています")
        required = tuple(dict.fromkeys(self.required_checks))
        if any(not isinstance(item, str) or not item.strip() for item in required):
            raise ValueError("required_checksは空でない文字列の列である必要があります")
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "required_checks", required)

    @property
    def missing_checks(self) -> Tuple[str, ...]:
        ids = {item.check_id for item in self.records}
        return tuple(item for item in self.required_checks if item not in ids)

    @property
    def detected_checks(self) -> Tuple[str, ...]:
        return tuple(item.check_id for item in self.records if item.status == ConditionalRuptureStatus.DETECTED)

    @property
    def unresolved_checks(self) -> Tuple[str, ...]:
        return tuple(item.check_id for item in self.records if item.status in (
            ConditionalRuptureStatus.UNRESOLVED, ConditionalRuptureStatus.NOT_EVALUATED,
        ))

    @property
    def complete(self) -> bool:
        return bool(self.records) and not self.missing_checks and not self.detected_checks and not self.unresolved_checks


def inspect_conditional_rupture_coverage(
    records: Tuple[ConditionalRuptureRecord, ...],
    *,
    required_checks: Tuple[str, ...] = (),
) -> ConditionalRuptureCoverage:
    return ConditionalRuptureCoverage(records, required_checks)


def record_conditional_rupture(
    candidate: ConditionalRelationCandidate,
    status: ConditionalRuptureStatus,
    context: BoundaryContext,
    *,
    check_id: str,
    reason: str = "",
    provenance: Optional[Provenance] = None,
) -> ConditionalRuptureRecord:
    """Record a candidate rupture without mutating or promoting the candidate."""
    return ConditionalRuptureRecord(candidate, status, context, check_id, reason, provenance)


def derive_relation_pattern(cluster: RelationClusterCandidate) -> RelationPatternCandidate:
    """Extract only slot values shared by every cluster member."""
    if not isinstance(cluster, RelationClusterCandidate):
        raise TypeError("clusterはRelationClusterCandidateである必要があります")
    if not cluster.members:
        raise ValueError("空のRelation clusterからPatternを生成できません")
    first = cluster.members[0]
    return RelationPatternCandidate(
        cluster=cluster,
        subject=first.subject if all(item.subject == first.subject for item in cluster.members) else None,
        relation=first.relation if all(item.relation == first.relation for item in cluster.members) else None,
        object=first.object if all(item.object == first.object for item in cluster.members) else None,
    )


@dataclass(frozen=True)
class StructureDelta:
    """Finite relation-structure difference between two candidates."""

    previous: StructureCandidate
    current: StructureCandidate

    @property
    def added(self) -> Tuple[RelationSemanticKey, ...]:
        return tuple(item for item in self.current.relations if item not in self.previous.relations)

    @property
    def removed(self) -> Tuple[RelationSemanticKey, ...]:
        return tuple(item for item in self.previous.relations if item not in self.current.relations)

    @property
    def unchanged(self) -> Tuple[RelationSemanticKey, ...]:
        return tuple(item for item in self.current.relations if item in self.previous.relations)

    @property
    def constraint_deltas(self) -> Tuple["RelationConstraintDelta", ...]:
        return tuple(
            RelationConstraintDelta(old, new)
            for correspondence in self.profile_correspondences
            for old, new in correspondence.matched
        )

    @property
    def profile_correspondences(self) -> Tuple["ProfileCorrespondence", ...]:
        previous = self.previous.supporting_profiles + self.previous.conflicting_profiles
        current = self.current.supporting_profiles + self.current.conflicting_profiles
        keys = tuple(dict.fromkeys(item.identity.semantic_key for item in previous + current))
        return tuple(
            correspond_profiles(previous, current, key)
            for key in keys if key in self.unchanged
        )


@dataclass(frozen=True)
class RelationConstraintDelta:
    """Finite change record for one semantic relation constraint."""

    previous: RelationConstraintProfile
    current: RelationConstraintProfile

    @property
    def strength_changed(self) -> bool:
        return self.previous.strength.value != self.current.strength.value

    @property
    def polarity_changed(self) -> bool:
        return self.previous.strength.support != self.current.strength.support

    @property
    def provenance_changed(self) -> bool:
        return self.previous.identity.provenance != self.current.identity.provenance

    @property
    def unresolved_changed(self) -> bool:
        previous = self.previous.strength.support == EvidencePolarity.UNRESOLVED
        current = self.current.strength.support == EvidencePolarity.UNRESOLVED
        return previous != current


@dataclass(frozen=True)
class ProfileCorrespondence:
    semantic_key: RelationSemanticKey
    matched: Tuple[Tuple[RelationConstraintProfile, RelationConstraintProfile], ...]
    unmatched_previous: Tuple[RelationConstraintProfile, ...]
    unmatched_current: Tuple[RelationConstraintProfile, ...]


def correspond_profiles(
    previous: Tuple[RelationConstraintProfile, ...],
    current: Tuple[RelationConstraintProfile, ...],
    semantic_key: RelationSemanticKey,
) -> ProfileCorrespondence:
    old = [item for item in previous if item.identity.semantic_key == semantic_key]
    new = [item for item in current if item.identity.semantic_key == semantic_key]
    pairs = []
    used = set()
    for old_item in old:
        source = old_item.identity.provenance.source if old_item.identity.provenance else None
        match_index = next(
            (index for index, item in enumerate(new)
             if index not in used
             and source is not None
             and (item.identity.provenance.source if item.identity.provenance else None) == source),
            None,
        )
        if match_index is not None:
            used.add(match_index)
            pairs.append((old_item, new[match_index]))
    matched_old_indexes = {index for index, item in enumerate(old) if any(item is pair[0] for pair in pairs)}
    matched_old_indexes = {index for index, item in enumerate(old) if any(item is pair[0] for pair in pairs)}
    matched_new_indexes = {index for index, item in enumerate(new) if any(item is pair[1] for pair in pairs)}
    return ProfileCorrespondence(
        semantic_key, tuple(pairs),
        tuple(item for index, item in enumerate(old) if index not in matched_old_indexes),
        tuple(item for index, item in enumerate(new) if index not in matched_new_indexes),
    )


@dataclass(frozen=True)
class RecompiledStructureCandidate:
    previous: Optional[StructureCandidate]
    current: StructureCandidate
    delta: Optional[StructureDelta] = None

    def __post_init__(self) -> None:
        if self.previous is None and self.delta is not None:
            raise ValueError("previousなしのRecompiledStructureCandidateにdeltaは指定できません")
        if self.previous is not None:
            if self.delta is None:
                raise ValueError("previousありのRecompiledStructureCandidateにはdeltaが必要です")
            if self.delta.previous != self.previous or self.delta.current != self.current:
                raise ValueError("RecompiledStructureCandidateのdeltaが候補と一致していません")


def compare_structure_candidates(
    previous: StructureCandidate,
    current: StructureCandidate,
) -> StructureDelta:
    """Record candidate structure change without interpreting its cause."""
    if previous.context != current.context:
        raise ValueError("Structure比較のBoundaryが一致していません")
    return StructureDelta(previous, current)


@dataclass(frozen=True)
class FunctionCandidate:
    """Candidate Function derived from structure, pending validation."""

    function: FunctionDescription
    invocation: FunctionInvocation
    structure: StructureCandidate
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        if self.invocation.function != self.function:
            raise ValueError("FunctionCandidateのFunctionとInvocationが一致していません")
        if self.invocation.context != self.structure.context:
            raise ValueError("FunctionCandidateのBoundaryがStructureと一致していません")


@dataclass(frozen=True)
class CompilationRecord:
    """Validation record for compiling a Function candidate; not Promotion."""

    candidate: FunctionCandidate
    validated: bool
    validation_context: BoundaryContext
    provenance: Optional[Provenance] = None
    validation_status: Optional[CompilationValidationStatus] = None

    def __post_init__(self) -> None:
        if not isinstance(self.validated, bool):
            raise TypeError("validatedはboolである必要があります")
        status = self.validation_status
        if status is None:
            status = CompilationValidationStatus.PASSED if self.validated else CompilationValidationStatus.NOT_EVALUATED
        if not isinstance(status, CompilationValidationStatus):
            raise TypeError("validation_statusはCompilationValidationStatusである必要があります")
        if status == CompilationValidationStatus.PASSED and not self.validated:
            raise ValueError("PASSEDのCompilationRecordはvalidated=Trueである必要があります")
        if status != CompilationValidationStatus.PASSED and self.validated:
            raise ValueError("PASSED以外のCompilationRecordはvalidated=Falseである必要があります")
        object.__setattr__(self, "validation_status", status)


@dataclass(frozen=True)
class CompiledMB:
    """Validated compiled local M_B; creation requires an explicit pass."""

    function: FunctionDescription
    structure: StructureCandidate
    validation: CompilationRecord

    def __post_init__(self) -> None:
        if self.validation.candidate.function != self.function:
            raise ValueError("CompiledMBのFunctionとValidation候補が一致していません")
        if self.validation.candidate.structure != self.structure:
            raise ValueError("CompiledMBのStructureとValidation候補が一致していません")
        if self.validation.validation_status != CompilationValidationStatus.PASSED:
            raise ValueError("CompiledMBにはPASSEDのCompilationRecordが必要です")


def extract_structure_candidate(
    profile: AdaptiveMBProfile,
    context: BoundaryContext,
    *,
    provenance: Optional[Provenance] = None,
    similarity: Optional[SimilarityVector] = None,
) -> StructureCandidate:
    """Extract unique semantic relation keys without creating a Commitment."""
    relations = tuple(dict.fromkeys(item.identity.semantic_key for item in profile.profiles))
    supporting = tuple(item for item in profile.profiles if item.strength.support == EvidencePolarity.SUPPORT)
    conflicting = tuple(item for item in profile.profiles if item.strength.support == EvidencePolarity.OPPOSE)
    unresolved = sum(item.strength.support == EvidencePolarity.UNRESOLVED for item in profile.profiles)
    return StructureCandidate(
        relations, context, provenance=provenance or profile.provenance,
        supporting_profiles=supporting, conflicting_profiles=conflicting,
        unresolved_count=unresolved, similarity=similarity,
    )


def induce_structure_candidate(
    profile: AdaptiveMBProfile,
    context: BoundaryContext,
    similarity_observations: Tuple[RelationSimilarityObservation, ...],
    *,
    min_score: float = 0.5,
    provenance: Optional[Provenance] = None,
) -> StructureInductionResult:
    """Use explicit similarity observations to record common and exceptional relations."""
    if not isinstance(min_score, (int, float)) or not 0.0 <= min_score <= 1.0:
        raise ValueError("min_scoreは0以上1以下である必要があります")
    observations = tuple(similarity_observations)
    if any(not isinstance(item, RelationSimilarityObservation) for item in observations):
        raise TypeError("similarity_observationsはRelationSimilarityObservationの列である必要があります")
    common = []
    exceptional = []
    unresolved = []
    examined = set()
    profile_keys = {item.identity.semantic_key for item in profile.profiles}
    for observation in observations:
        left = observation.left.identity.semantic_key
        right = observation.right.identity.semantic_key
        examined.update((left, right))
        if observation.status == SimilarityObservationStatus.UNRESOLVED:
            unresolved.append(observation)
        elif observation.status == SimilarityObservationStatus.NOT_COVERED:
            continue
        elif observation.status == SimilarityObservationStatus.SIMILAR and observation.score >= min_score:
            for key in (left, right):
                if key in profile_keys and key not in common:
                    common.append(key)
        else:
            for key in (left, right):
                if key in profile_keys and key not in exceptional:
                    exceptional.append(key)
    all_relations = tuple(dict.fromkeys(item.identity.semantic_key for item in profile.profiles))
    unexamined = tuple(item for item in all_relations if item not in examined)
    relations = all_relations
    candidate = extract_structure_candidate(
        profile, context, provenance=provenance, similarity=None,
    )
    return StructureInductionResult(
        candidate=StructureCandidate(
            relations=relations or candidate.relations,
            context=candidate.context,
            provenance=candidate.provenance,
            supporting_profiles=candidate.supporting_profiles,
            conflicting_profiles=candidate.conflicting_profiles,
            unresolved_count=candidate.unresolved_count + len(unresolved),
        ),
        common_relations=tuple(common),
        exception_relations=tuple(exceptional),
        unresolved_observations=tuple(unresolved),
        unexamined_relations=unexamined,
    )


def induce_structure_candidate_with_clusters(
    profile: AdaptiveMBProfile,
    context: BoundaryContext,
    similarity_observations: Tuple[RelationSimilarityObservation, ...],
    relation_clusters: Tuple[RelationClusterCandidate, ...],
    *,
    min_score: float = 0.5,
    provenance: Optional[Provenance] = None,
) -> StructureInductionResult:
    """Attach relation-position cluster evidence to a bounded structure result."""
    base = induce_structure_candidate(
        profile, context, similarity_observations,
        min_score=min_score, provenance=provenance,
    )
    clusters = tuple(relation_clusters)
    if any(cluster.context != context for cluster in clusters):
        raise ValueError("Relation clusterのBoundaryがStructure誘導と一致していません")
    return StructureInductionResult(
        candidate=base.candidate,
        common_relations=base.common_relations,
        exception_relations=base.exception_relations,
        unresolved_observations=base.unresolved_observations,
        unexamined_relations=base.unexamined_relations,
        cluster_candidates=clusters,
    )
def cluster_relation_keys(
    keys: Tuple[RelationSemanticKey, ...],
    observations: Tuple[RelationSemanticSimilarityObservation, ...],
    context: BoundaryContext,
    *,
    min_score: float = 0.5,
    provenance: Optional[Provenance] = None,
) -> Tuple[RelationClusterCandidate, ...]:
    """Build connected relation-cluster candidates from explicit position observations."""
    if not isinstance(min_score, (int, float)) or not 0.0 <= min_score <= 1.0:
        raise ValueError("min_scoreは0以上1以下である必要があります")
    known = tuple(dict.fromkeys(keys))
    if any(not isinstance(item, RelationSemanticKey) for item in known):
        raise TypeError("keysはRelationSemanticKeyの列である必要があります")
    valid = tuple(
        item for item in observations
        if isinstance(item, RelationSemanticSimilarityObservation)
        and item.status == SimilarityObservationStatus.SIMILAR
        and item.score >= min_score
    )
    groups = []
    for observation in valid:
        pair = {observation.left, observation.right}
        if not pair.issubset(set(known)) or observation.left == observation.right:
            continue
        merged = [index for index, group in enumerate(groups) if pair.intersection(group[0])]
        if not merged:
            groups.append((set(pair), [observation]))
            continue
        first = merged[0]
        groups[first][0].update(pair)
        groups[first][1].append(observation)
        for index in reversed(merged[1:]):
            groups[first][0].update(groups[index][0])
            groups[first][1].extend(groups[index][1])
            groups.pop(index)
    results = []
    for members, supporting in groups:
        member_tuple = tuple(item for item in known if item in members)
        incident = tuple(
            item for item in observations
            if item.left in members and item.right in members and item.left != item.right
        )
        results.append(RelationClusterCandidate(
            members=member_tuple,
            observations=incident,
            context=context,
            provenance=provenance,
            conflicting_edges=tuple(
                item for item in incident if item.status == SimilarityObservationStatus.NOT_SIMILAR
            ),
            unresolved_edges=tuple(
                item for item in incident if item.status == SimilarityObservationStatus.UNRESOLVED
            ),
        ))
    return tuple(results)


def extract_recompiled_structure_candidate(
    profile: AdaptiveMBProfile,
    context: BoundaryContext,
    *,
    similarity: Optional[SimilarityVector] = None,
    provenance: Optional[Provenance] = None,
) -> RecompiledStructureCandidate:
    """Build a vNext structure and compare it only within a shared Boundary."""
    current = extract_structure_candidate(
        profile, context, similarity=similarity, provenance=provenance,
    )
    previous = profile.prior_structure
    delta = compare_structure_candidates(previous, current) if previous and previous.context == context else None
    return RecompiledStructureCandidate(previous, current, delta)


def compile_function_candidate(
    structure: StructureCandidate,
    function: FunctionDescription,
    *,
    purpose: str = "",
    config: Optional[dict] = None,
    provenance: Optional[Provenance] = None,
) -> FunctionCandidate:
    """Create a pending Function candidate; this does not validate or promote it."""
    invocation = FunctionInvocation(
        function, structure.context, purpose=purpose,
        config=config or {}, provenance=provenance or structure.provenance,
    )
    return FunctionCandidate(function, invocation, structure, provenance=provenance or structure.provenance)


def record_compilation_validation(
    candidate: FunctionCandidate,
    status: CompilationValidationStatus,
    validation_context: BoundaryContext,
    *,
    provenance: Optional[Provenance] = None,
) -> CompilationRecord:
    """Record validation without promoting the candidate to an active Function."""
    if not isinstance(status, CompilationValidationStatus):
        raise TypeError("statusはCompilationValidationStatusである必要があります")
    return CompilationRecord(
        candidate=candidate,
        validated=status == CompilationValidationStatus.PASSED,
        validation_context=validation_context,
        provenance=provenance or candidate.provenance,
        validation_status=status,
    )


def compile_validated_candidate(record: CompilationRecord) -> CompiledMB:
    """Materialize Compiled M_B only after explicit validation success."""
    return CompiledMB(
        function=record.candidate.function,
        structure=record.candidate.structure,
        validation=record,
    )
