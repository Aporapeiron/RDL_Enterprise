"""Minimal relation and node description types for RDL Core."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from .constraint_types import ConstraintIdentity
from .contracts import BoundaryContext, FrozenBoundaryValue, Provenance, freeze_boundary_value


class RelationObservationStatus(str, Enum):
    OBSERVED = "observed"
    NOT_OBSERVED = "not_observed"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class NodeDescription:
    """A describable node; creation does not imply Commitment."""

    node_id: str
    domain: str
    relations: Tuple[ConstraintIdentity, ...] = ()
    attributes: Tuple[Tuple[str, FrozenBoundaryValue], ...] = ()
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, str) or not self.node_id.strip():
            raise ValueError("node_id は空にできません")
        if not isinstance(self.domain, str) or not self.domain.strip():
            raise ValueError("domain は空にできません")
        if any(not isinstance(relation, ConstraintIdentity) for relation in self.relations):
            raise TypeError("relationsはConstraintIdentityのtupleである必要があります")
        object.__setattr__(self, "relations", tuple(self.relations))
        for key, _ in self.attributes:
            if not isinstance(key, str) or not key.strip():
                raise ValueError("attributesのキーは空にできません")
        object.__setattr__(
            self,
            "attributes",
            tuple((key, freeze_boundary_value(value)) for key, value in self.attributes),
        )


@dataclass(frozen=True)
class RelationObservation:
    """An observed relation attached to a node under a finite boundary."""

    node: NodeDescription
    relation: ConstraintIdentity
    boundary: BoundaryContext
    status: RelationObservationStatus

    def __post_init__(self) -> None:
        if not isinstance(self.status, RelationObservationStatus):
            raise TypeError("statusはRelationObservationStatusである必要があります")
        if self.relation not in self.node.relations:
            raise ValueError("観測対象RelationはNodeDescriptionに登録されている必要があります")


@dataclass(frozen=True)
class NodeDescriptionGraph:
    """Minimal immutable graph of Core node descriptions."""

    nodes: Tuple[NodeDescription, ...] = ()

    def __post_init__(self) -> None:
        nodes = tuple(self.nodes)
        if any(not isinstance(node, NodeDescription) for node in nodes):
            raise TypeError("nodesはNodeDescriptionのtupleである必要があります")
        ids = [node.node_id for node in nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("NodeDescriptionGraph内のnode_idは一意である必要があります")
        object.__setattr__(self, "nodes", nodes)

    def get(self, node_id: str) -> Optional[NodeDescription]:
        return next((node for node in self.nodes if node.node_id == node_id), None)

    @property
    def internal_relation_targets(self) -> Tuple[str, ...]:
        node_ids = {node.node_id for node in self.nodes}
        targets = {
            relation.object
            for node in self.nodes
            for relation in node.relations
            if relation.object in node_ids
        }
        return tuple(sorted(targets))

    @property
    def external_relation_targets(self) -> Tuple[str, ...]:
        node_ids = {node.node_id for node in self.nodes}
        targets = {
            relation.object
            for node in self.nodes
            for relation in node.relations
            if relation.object not in node_ids
        }
        return tuple(sorted(targets))
