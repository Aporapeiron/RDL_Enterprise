"""Projection of the Enterprise exact-key trigger slice into RDL Core."""

from typing import Optional

from rdl_core import (
    BoundaryContext,
    MatchingObservation,
    Provenance,
    TriggerDescription,
    observe_exact_keys,
)

from .mb_graph_adapter import provenance_from_mbnode


def trigger_description_from_mbnode(node: object) -> TriggerDescription:
    """Project only ``trigger_pattern.exact_keys``; leave other policies in Enterprise."""
    pattern = getattr(node, "trigger_pattern", None)
    if not isinstance(pattern, dict):
        raise TypeError("MBNode.trigger_patternはmappingである必要があります")
    exact_keys = pattern.get("exact_keys", ())
    if exact_keys is None:
        exact_keys = ()
    if isinstance(exact_keys, str) or not isinstance(exact_keys, (list, tuple)):
        raise TypeError("trigger_pattern.exact_keysは文字列列またはタプルである必要があります")
    return TriggerDescription(tuple(exact_keys))


def matching_observation_from_mbnode(
    node: object,
    query: str,
    boundary: BoundaryContext,
    *,
    provenance: Optional[Provenance] = None,
) -> MatchingObservation:
    """Observe exact-key matching under the supplied finite boundary."""
    resolved_provenance = provenance if provenance is not None else provenance_from_mbnode(node)
    return observe_exact_keys(
        trigger_description_from_mbnode(node), query, boundary,
        provenance=resolved_provenance,
    )
