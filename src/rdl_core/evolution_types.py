"""Bounded types for adaptive structure and Function compilation candidates."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from .constraint_types import RelationSemanticKey
from .contracts import BoundaryContext, EvidencePolarity, Provenance
from .function_types import FunctionDescription, FunctionInvocation
from .similarity_types import RelationConstraintProfile


class SimilarityMetric(str, Enum):
    STRENGTH = "strength"
    POLARITY = "polarity"
    PROVENANCE = "provenance"


class CompilationValidationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNRESOLVED = "unresolved"
    NOT_EVALUATED = "not_evaluated"


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
             and (item.identity.provenance.source if item.identity.provenance else None) == source),
            None,
        )
        if match_index is not None:
            used.add(match_index)
            pairs.append((old_item, new[match_index]))
    matched_old_indexes = {index for index, item in enumerate(old) if any(item is pair[0] for pair in pairs)}
    remaining_old = [item for index, item in enumerate(old) if index not in matched_old_indexes]
    remaining_new = [item for index, item in enumerate(new) if index not in used]
    for old_item, new_item in zip(remaining_old, remaining_new):
        pairs.append((old_item, new_item))
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
