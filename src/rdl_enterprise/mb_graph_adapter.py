"""Projection from Enterprise MBNode objects to RDL Core relation types."""

from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

from rdl_core import (
    BoundaryContext,
    ConstraintIdentity,
    NodeDescription,
    NodeDescriptionGraph,
    Provenance,
    RelationObservation,
    RelationObservationStatus,
)


_RELATION_STATUS = {
    "support": RelationObservationStatus.OBSERVED,
    "contradict": RelationObservationStatus.OBSERVED,
    "independent": RelationObservationStatus.OBSERVED,
    "unknown": RelationObservationStatus.UNRESOLVED,
}


@dataclass(frozen=True)
class MBGraphProjection:
    graph: NodeDescriptionGraph
    observations: Tuple[RelationObservation, ...]


def provenance_from_mbnode(node: object) -> Optional[Provenance]:
    """Recover MBNode source fields without inventing a missing source."""
    source_id = getattr(node, "source_id", None)
    lineage = getattr(node, "source_lineage", None)
    source_id = source_id.strip() if isinstance(source_id, str) else source_id
    lineage = lineage.strip() if isinstance(lineage, str) else lineage
    if source_id is None and lineage is None:
        return None
    if not source_id and not lineage:
        return None
    return Provenance(source=str(source_id or lineage), lineage=lineage or None)


def relations_from_mbnode(node: object) -> Tuple[ConstraintIdentity, ...]:
    """Translate explicit MBNode relation edges into Core identities."""
    node_id = getattr(node, "id", None)
    edges = getattr(node, "node_relations", None)
    if not isinstance(node_id, str) or not node_id.strip():
        raise ValueError("MBNode.id は必須です")
    if edges is None:
        return ()
    if not isinstance(edges, dict):
        raise TypeError("MBNode.node_relations はmappingである必要があります")
    relations = []
    for target_id, relation_kind in edges.items():
        if not isinstance(target_id, str) or not target_id.strip():
            raise ValueError("MBNode.node_relationsのtarget idは非空文字列である必要があります")
        if relation_kind not in _RELATION_STATUS:
            raise ValueError(f"未知のMBNode relation kindです: {relation_kind}")
        relations.append(ConstraintIdentity(
            constraint_id=f"{node_id}:{relation_kind}:{target_id}",
            subject=node_id,
            relation=relation_kind,
            object=target_id,
        ))
    return tuple(relations)


def relation_observations_from_mbnode(
    node: object,
    boundary: BoundaryContext,
    *,
    provenance: Optional[Provenance] = None,
) -> Tuple[RelationObservation, ...]:
    """Project MBNode relation edges as status-separated Core observations."""
    resolved_provenance = provenance if provenance is not None else provenance_from_mbnode(node)
    relations = relations_from_mbnode(node)
    description = node_description_from_mbnode(node, relations, provenance=resolved_provenance)
    return tuple(
        RelationObservation(
            description, relation, boundary, _RELATION_STATUS[relation.relation]
        )
        for relation in relations
    )


def project_mbgraph(
    mb_graph: object,
    boundary: BoundaryContext,
    *,
    provenance: Optional[Provenance] = None,
) -> MBGraphProjection:
    """Project the selected semantic slice of an Enterprise MBGraph."""
    nodes = getattr(mb_graph, "nodes", None)
    if not isinstance(nodes, dict):
        raise TypeError("MBGraph.nodes はmappingである必要があります")
    descriptions = []
    observations = []
    for key, node in nodes.items():
        if getattr(node, "id", None) != key:
            raise ValueError("MBGraph.nodesのkeyとMBNode.idが一致していません")
        node_provenance = provenance if provenance is not None else provenance_from_mbnode(node)
        relations = relations_from_mbnode(node)
        descriptions.append(node_description_from_mbnode(node, relations, provenance=node_provenance))
        observations.extend(
            relation_observations_from_mbnode(node, boundary, provenance=node_provenance)
        )
    return MBGraphProjection(NodeDescriptionGraph(tuple(descriptions)), tuple(observations))


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
        provenance=provenance if provenance is not None else provenance_from_mbnode(node),
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
