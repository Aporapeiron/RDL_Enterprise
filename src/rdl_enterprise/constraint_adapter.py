"""Adapter from Enterprise constraint evaluation results to RDL Core types."""

from typing import Optional

from rdl_core import (
    AuthorityConstraint,
    BoundaryContext,
    ConstraintActivation,
    ConstraintIdentity,
    ConstraintStrength,
    EvidencePolarity,
)


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
        value=float(getattr(bundle, "constraint_score", 0.0)),
        support=support,
        relevance=float(getattr(bundle, "relevance", 0.0)),
        freshness=float(getattr(bundle, "freshness", 0.0)),
        authority=float(getattr(bundle, "authority_weight", 0.0)),
    )
    return ConstraintActivation(
        identity=identity,
        context=context,
        strength=strength,
        authority_constraint=authority_constraint,
    )
