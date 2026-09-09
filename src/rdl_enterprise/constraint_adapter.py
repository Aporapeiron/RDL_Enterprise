"""Adapter from Enterprise constraint evaluation results to RDL Core types."""

from typing import Optional

from rdl_core import (
    AuthorityConstraint,
    BoundaryContext,
    ConstraintActivation,
    ConstraintEvaluation,
    ConstraintIdentity,
    ConstraintStrength,
    EvidencePolarity,
    ConstraintEvaluationWeights,
    evaluate_constraint_strength,
    record_constraint_evaluation,
)


def _required_score(bundle: object, field_name: str) -> float:
    """Read a required observed score without collapsing missing into zero."""
    if not hasattr(bundle, field_name):
        raise ValueError(f"ConstraintBundle に必須観測値 '{field_name}' がありません")
    value = getattr(bundle, field_name)
    if value is None:
        raise ValueError(f"ConstraintBundle の観測値 '{field_name}' は未確定です")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"ConstraintBundle の観測値 '{field_name}' は数値である必要があります") from exc


def activation_from_bundle(
    bundle: object,
    identity: ConstraintIdentity,
    context: BoundaryContext,
    *,
    authority_constraint: Optional[AuthorityConstraint] = None,
    support: EvidencePolarity = EvidencePolarity.UNRESOLVED,
) -> ConstraintActivation:
    """Map a bounded Enterprise bundle result without committing it to M_B.

    The adapter deliberately does not infer truth, Commitment, or polarity from
    numeric scores. Polarity must be supplied explicitly by the caller.
    """
    strength = ConstraintStrength(
        value=_required_score(bundle, "constraint_score"),
        support=support,
        relevance=_required_score(bundle, "relevance"),
        freshness=_required_score(bundle, "freshness"),
        authority=_required_score(bundle, "authority_weight"),
    )
    return ConstraintActivation(
        identity=identity,
        context=context,
        strength=strength,
        authority_constraint=authority_constraint,
    )


def evaluate_bundle_strength(
    bundle: object,
    *,
    support: EvidencePolarity = EvidencePolarity.UNRESOLVED,
    weights: Optional[ConstraintEvaluationWeights] = None,
) -> ConstraintStrength:
    """Re-evaluate bundle components with the Core pure function.

    The Enterprise-produced ``constraint_score`` is intentionally not used as
    an input. This makes drift between the legacy evaluator and the Core
    evaluator observable instead of silently preserving it.
    """
    return evaluate_constraint_strength(
        relevance=_required_score(bundle, "relevance"),
        freshness=_required_score(bundle, "freshness"),
        authority=_required_score(bundle, "authority_weight"),
        source=_required_score(bundle, "source_strength"),
        convergence=_required_score(bundle, "convergence"),
        support=support,
        weights=weights or ConstraintEvaluationWeights(),
    )


def record_bundle_evaluation(
    bundle: object,
    context: BoundaryContext,
    *,
    support: EvidencePolarity = EvidencePolarity.UNRESOLVED,
    weights: Optional[ConstraintEvaluationWeights] = None,
    provenance=None,
) -> ConstraintEvaluation:
    """Retain Bundle components and evaluation weights as a recoverable record."""
    if provenance is None:
        raise ValueError("Enterprise bundle evaluationにはProvenanceが必要です")
    return record_constraint_evaluation(
        context=context,
        relevance=_required_score(bundle, "relevance"),
        freshness=_required_score(bundle, "freshness"),
        authority=_required_score(bundle, "authority_weight"),
        source=_required_score(bundle, "source_strength"),
        convergence=_required_score(bundle, "convergence"),
        support=support,
        weights=weights or ConstraintEvaluationWeights(),
        evaluator_id="rdl_enterprise.bundle_constraint",
        evaluator_version="1",
        provenance=provenance,
    )
