"""Bounded types for adaptive structure and Function compilation candidates."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from .constraint_types import RelationSemanticKey
from .contracts import BoundaryContext, Provenance
from .function_types import FunctionDescription, FunctionInvocation
from .similarity_types import RelationConstraintProfile


class SimilarityMetric(str, Enum):
    STRENGTH = "strength"
    POLARITY = "polarity"
    PROVENANCE = "provenance"


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

    def __post_init__(self) -> None:
        relations = tuple(self.relations)
        if any(not isinstance(relation, RelationSemanticKey) for relation in relations):
            raise TypeError("relationsはRelationSemanticKeyの列である必要があります")
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

    def __post_init__(self) -> None:
        if not isinstance(self.validated, bool):
            raise TypeError("validatedはboolである必要があります")
