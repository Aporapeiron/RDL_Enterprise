"""Recompilation requests derived from immutable lifecycle observations."""

from dataclasses import dataclass
from typing import Optional

from .activation_types import ActiveCompiledMB
from .contracts import BoundaryContext, Provenance
from .deactivation_types import DeactivationRecord
from .function_types import FunctionDescription
from .similarity_types import RelationConstraintProfile
from .evolution_types import AdaptiveMBProfile
from .evolution_types import (
    CompiledMB,
    FunctionCandidate,
    StructureCandidate,
    StructureDelta,
    compare_structure_candidates,
    compile_function_candidate,
)


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


@dataclass(frozen=True)
class ReplacementCandidate:
    """Candidate lineage from a prior Compiled M_B to a vNext Function."""

    predecessor: CompiledMB
    request: RecompilationRequest
    candidate: FunctionCandidate
    structure_delta: StructureDelta

    def __post_init__(self) -> None:
        if self.request.active.artifact != self.predecessor:
            raise ValueError("ReplacementCandidateのRequestとpredecessorが一致していません")
        if self.candidate.function == self.predecessor.function:
            raise ValueError("ReplacementCandidateには新しいFunction identity/versionが必要です")


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


def reintroduce_to_adaptive(
    request: RecompilationRequest,
    profiles: tuple[RelationConstraintProfile, ...],
    context: BoundaryContext,
    *,
    provenance: Optional[Provenance] = None,
) -> AdaptiveMBProfile:
    """Return new finite observations to Adaptive M_B for later inspection."""
    if not isinstance(profiles, tuple):
        profiles = tuple(profiles)
    return AdaptiveMBProfile(
        profiles=profiles,
        context=context,
        provenance=provenance or request.provenance,
        prior_structure=request.active.artifact.structure,
        recompilation_reason=request.reason,
    )


def compile_replacement_candidate(
    request: RecompilationRequest,
    structure: StructureCandidate,
    function: FunctionDescription,
    *,
    purpose: str = "recompilation",
    config: Optional[dict] = None,
    provenance: Optional[Provenance] = None,
) -> FunctionCandidate:
    """Build a vNext candidate from a recompiled structure, without promotion."""
    return compile_function_candidate(
        structure, function, purpose=purpose, config=config,
        provenance=provenance or request.provenance,
    )


def record_replacement_candidate(
    request: RecompilationRequest,
    structure: StructureCandidate,
    function: FunctionDescription,
    *,
    purpose: str = "recompilation",
    config: Optional[dict] = None,
    provenance: Optional[Provenance] = None,
) -> ReplacementCandidate:
    candidate = compile_replacement_candidate(
        request, structure, function, purpose=purpose,
        config=config, provenance=provenance,
    )
    delta = compare_structure_candidates(request.active.artifact.structure, structure)
    return ReplacementCandidate(request.active.artifact, request, candidate, delta)
