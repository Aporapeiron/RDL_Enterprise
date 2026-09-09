"""Projection from Enterprise MBNode objects to RDL Core relation types."""

from typing import Iterable, Optional, Tuple

from rdl_core import (
    BoundaryContext,
    ConstraintIdentity,
    NodeDescription,
    Provenance,
    RelationObservation,
    RelationObservationStatus,
)


def node_description_from_mbnode(
    node: object,
    relations: Iterable[ConstraintIdentity] = (),
    *,
    provenance: Optional[Provenance] = None,
) -> NodeDescription:
    """Project only the bounded semantic identity of an Enterprise MBNode."""
    node_id = getattr(node, "id", None)
    domain = getattr(node, "domain", None)
    if not isinstance(node_id, str) or not node_id.strip():
        raise ValueError("MBNode.id は必須です")
    if not isinstance(domain, str) or not domain.strip():
        raise ValueError("MBNode.domain は必須です")
    return NodeDescription(
        node_id=node_id,
        domain=domain,
        relations=tuple(relations),
        provenance=provenance,
    )


def relation_observation_from_mbnode(
    node: object,
    relation: ConstraintIdentity,
    boundary: BoundaryContext,
    status: RelationObservationStatus,
    *,
    provenance: Optional[Provenance] = None,
) -> RelationObservation:
    """Create a Core observation from an explicitly selected MBNode relation."""
    description = node_description_from_mbnode(node, (relation,), provenance=provenance)
    return RelationObservation(description, relation, boundary, status)
