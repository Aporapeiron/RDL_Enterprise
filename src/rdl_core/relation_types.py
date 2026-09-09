"""Minimal relation and node description types for RDL Core."""

from dataclasses import dataclass
from typing import Optional, Tuple

from .constraint_types import ConstraintIdentity
from .contracts import BoundaryInputValue, Provenance


@dataclass(frozen=True)
class NodeDescription:
    """A describable node; creation does not imply Commitment."""

    node_id: str
    domain: str
    relations: Tuple[ConstraintIdentity, ...] = ()
    attributes: Tuple[Tuple[str, BoundaryInputValue], ...] = ()
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, str) or not self.node_id.strip():
            raise ValueError("node_id は空にできません")
        if not isinstance(self.domain, str) or not self.domain.strip():
            raise ValueError("domain は空にできません")
        for key, _ in self.attributes:
            if not isinstance(key, str) or not key.strip():
                raise ValueError("attributesのキーは空にできません")


@dataclass(frozen=True)
class RelationObservation:
    """An observed relation attached to a node under a finite boundary."""

    node: NodeDescription
    relation: ConstraintIdentity
    boundary_id: str
    observed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.boundary_id, str) or not self.boundary_id.strip():
            raise ValueError("boundary_id は空にできません")
