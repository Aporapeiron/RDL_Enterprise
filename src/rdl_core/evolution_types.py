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

    def __post_init__(self) -> None:
        profiles = tuple(self.profiles)
        if any(not isinstance(profile, RelationConstraintProfile) for profile in profiles):
            raise TypeError("profilesはRelationConstraintProfileの列である必要があります")
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
