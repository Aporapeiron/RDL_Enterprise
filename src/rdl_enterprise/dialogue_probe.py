"""Enterprise-local dialogue probing primitives.

The module records finite observations and probe intent. It does not infer a
hidden true intention, diagnose a person, or mutate a model directly.
"""

from dataclasses import dataclass
from typing import Tuple

from rdl_core import (
    BoundaryContext,
    EvidencePolarity,
    FunctionDescription,
    Provenance,
    RelationConstraintProfile,
    StructureCandidate,
    AdaptiveMBProfile,
    extract_structure_candidate,
)


@dataclass(frozen=True)
class ProbeIntent:
    probe_type: str
    target_relations: Tuple[str, ...]
    purpose: str
    context: BoundaryContext
    avoid: Tuple[str, ...] = ()
    provenance: Provenance | None = None


@dataclass(frozen=True)
class GeneratedQuestion:
    intent: ProbeIntent
    text: str
    context: BoundaryContext
    provenance: Provenance | None = None


@dataclass(frozen=True)
class DialogueObservation:
    turn_id: str
    utterance: str
    profiles: Tuple[RelationConstraintProfile, ...]
    question: GeneratedQuestion | None
    context: BoundaryContext
    provenance: Provenance


@dataclass(frozen=True)
class DialogueStructureRevision:
    """A selected dialogue reconstruction, retaining the prior finite M_B."""

    previous: StructureCandidate | None
    current: StructureCandidate
    selected_turn_ids: Tuple[str, ...]
    context: BoundaryContext
    provenance: Provenance | None = None


def select_probe_intent(
    profiles: Tuple[RelationConstraintProfile, ...],
    context: BoundaryContext,
    *,
    provenance: Provenance | None = None,
) -> ProbeIntent:
    """Select what to distinguish next, without selecting an answer."""
    unresolved = tuple(
        profile.identity.semantic_key.relation
        for profile in profiles
        if profile.strength.support == EvidencePolarity.UNRESOLVED
    )
    targets = unresolved or ("condition-difference",)
    return ProbeIntent(
        probe_type="condition_comparison",
        target_relations=targets,
        purpose=context.purpose or "exploration",
        context=context,
        avoid=("diagnosis", "truth_claim", "forced_choice"),
        provenance=provenance,
    )


def generate_question(intent: ProbeIntent) -> GeneratedQuestion:
    """Generate fixture wording from intent; wording cannot mutate M_B."""
    if intent.probe_type == "condition_comparison":
        text = "忙しくない日や一人で作業する日でも、同じように感じますか？"
    elif intent.probe_type == "structure_reflection":
        text = "今の整理で、違うところや付け加えたいところはありますか？"
    else:
        text = "具体的な場面の違いを一つ教えてください。"
    return GeneratedQuestion(intent, text, intent.context, intent.provenance)


def record_human_response(
    turn_id: str,
    utterance: str,
    profiles: Tuple[RelationConstraintProfile, ...],
    context: BoundaryContext,
    provenance: Provenance,
    *,
    question: GeneratedQuestion | None = None,
) -> DialogueObservation:
    """Turn a fixture response into an observation before reconstruction."""
    if not isinstance(utterance, str) or not utterance.strip():
        raise ValueError("utteranceは空にできません")
    return DialogueObservation(turn_id, utterance, profiles, question, context, provenance)


def reconstruct_dialogue_structure(
    observations: Tuple[DialogueObservation, ...],
    context: BoundaryContext,
    *,
    provenance: Provenance | None = None,
) -> StructureCandidate:
    """Build a finite candidate from retained observations, not a conclusion."""
    profiles = tuple(profile for observation in observations for profile in observation.profiles)
    source = provenance or (observations[-1].provenance if observations else None)
    return extract_structure_candidate(
        AdaptiveMBProfile(profiles, context, provenance=source),
        context,
        provenance=source,
    )


def reconstruct_selected_dialogue_structure(
    observations: Tuple[DialogueObservation, ...],
    context: BoundaryContext,
    *,
    selected_turn_ids: Tuple[str, ...],
    previous: StructureCandidate | None = None,
    provenance: Provenance | None = None,
) -> DialogueStructureRevision:
    """Reconstruct from explicitly selected turns without deleting prior M_B."""
    requested = tuple(selected_turn_ids)
    if len(set(requested)) != len(requested):
        raise ValueError("selected_turn_idsに重複があります")
    observation_ids = tuple(observation.turn_id for observation in observations)
    if len(set(observation_ids)) != len(observation_ids):
        raise ValueError("observations内のturn_idが重複しています")
    available = {observation.turn_id: observation for observation in observations}
    missing = tuple(turn_id for turn_id in requested if turn_id not in available)
    if missing:
        raise ValueError(f"存在しないturn_idがSelectionされています: {missing}")
    wrong_boundary = tuple(
        turn_id for turn_id in requested
        if available[turn_id].context != context
    )
    if wrong_boundary:
        raise ValueError(f"現在Boundary外のturn_idがSelectionされています: {wrong_boundary}")
    selected = tuple(available[turn_id] for turn_id in requested)
    if not selected:
        raise ValueError("selected_turn_idsは現在BoundaryのObservationを少なくとも1件含む必要があります")
    source = provenance or selected[-1].provenance
    current = reconstruct_dialogue_structure(selected, context, provenance=source)
    return DialogueStructureRevision(
        previous=previous,
        current=current,
        selected_turn_ids=requested,
        context=context,
        provenance=source,
    )
