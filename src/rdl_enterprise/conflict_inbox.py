"""Enterprise-local read model for predicted structural conflicts.

This module records inspection output only.  It does not resolve conflicts,
change M_B, or infer risk/urgency from predicted heat.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Tuple


def _bounded_heat(value: float) -> float:
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise ValueError("predicted_heat must be between 0.0 and 1.0")
    return value


@dataclass(frozen=True)
class StructuralConflict:
    conflict_id: str
    case_id: str
    left_structure_id: str
    right_structure_id: str
    predicted_heat: float
    heat_components: Tuple[Tuple[str, float], ...] = ()
    authority_requirements: Tuple[str, ...] = ()
    support_by_structure: Tuple[Tuple[str, float], ...] = ()
    observation_status: str = "STRUCTURAL_CONFLICT"
    provenance: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.conflict_id or not self.case_id:
            raise ValueError("conflict_id and case_id are required")
        if not self.left_structure_id or not self.right_structure_id:
            raise ValueError("both structure ids are required")
        object.__setattr__(self, "predicted_heat", _bounded_heat(self.predicted_heat))
        object.__setattr__(
            self,
            "heat_components",
            tuple((str(name), _bounded_heat(value)) for name, value in self.heat_components),
        )
        object.__setattr__(
            self,
            "support_by_structure",
            tuple((str(name), _bounded_heat(value)) for name, value in self.support_by_structure),
        )


@dataclass(frozen=True)
class ConflictInboxItem:
    case_id: str
    conflicts: Tuple[StructuralConflict, ...]
    total_predicted_heat: float
    aggregation_method: str = "bounded_sum_v0_1"
    review_status: str = "STRUCTURAL_CONFLICT"
    human_routed: bool = False
    routed_actor_id: Optional[str] = None


@dataclass(frozen=True)
class ConflictInboxEvent:
    case_id: str
    event_type: str
    recorded_at: str
    conflict_count: int
    total_predicted_heat: float
    actor_id: Optional[str] = None


class StructuralConflictInbox:
    """Finite, in-memory inbox for human inspection of structural conflicts."""

    AGGREGATION_METHOD = "bounded_sum_v0_1"

    def __init__(self) -> None:
        self._conflicts: Dict[str, StructuralConflict] = {}
        self._history: list[ConflictInboxEvent] = []

    def add_conflict(self, conflict: StructuralConflict) -> ConflictInboxItem:
        self._conflicts[conflict.conflict_id] = conflict
        item = self.get_case(conflict.case_id)
        self._history.append(
            ConflictInboxEvent(
                case_id=item.case_id,
                event_type="conflict_recorded",
                recorded_at=datetime.utcnow().isoformat(),
                conflict_count=len(item.conflicts),
                total_predicted_heat=item.total_predicted_heat,
            )
        )
        return item

    def get_case(self, case_id: str) -> ConflictInboxItem:
        conflicts = tuple(c for c in self._conflicts.values() if c.case_id == case_id)
        if not conflicts:
            raise KeyError(case_id)
        return self._item(case_id, conflicts)

    def list_cases(self) -> Tuple[ConflictInboxItem, ...]:
        case_ids = {conflict.case_id for conflict in self._conflicts.values()}
        return tuple(sorted((self.get_case(case_id) for case_id in case_ids),
                            key=lambda item: item.total_predicted_heat, reverse=True))

    def detail(self, case_id: str) -> Dict[str, Any]:
        item = self.get_case(case_id)
        return {
            "case_id": item.case_id,
            "status": item.review_status,
            "total_predicted_heat": item.total_predicted_heat,
            "aggregation_method": item.aggregation_method,
            "conflicts": tuple({
                "conflict_id": c.conflict_id,
                "left_structure_id": c.left_structure_id,
                "right_structure_id": c.right_structure_id,
                "predicted_heat": c.predicted_heat,
                "heat_components": c.heat_components,
                "authority_requirements": c.authority_requirements,
                "support_by_structure": c.support_by_structure,
                "provenance": c.provenance,
            } for c in item.conflicts),
        }

    def route_to_human(self, case_id: str, authority: Any) -> ConflictInboxItem:
        item = self.get_case(case_id)
        if not authority.is_human_authenticated():
            raise PermissionError("human authentication is required")
        self._history.append(
            ConflictInboxEvent(case_id, "human_review_requested", datetime.utcnow().isoformat(),
                               len(item.conflicts), item.total_predicted_heat, authority.actor_id)
        )
        return ConflictInboxItem(item.case_id, item.conflicts, item.total_predicted_heat,
                                 item.aggregation_method, item.review_status, True, authority.actor_id)

    def history(self, case_id: Optional[str] = None) -> Tuple[ConflictInboxEvent, ...]:
        if case_id is None:
            return tuple(self._history)
        return tuple(event for event in self._history if event.case_id == case_id)

    @staticmethod
    def _item(case_id: str, conflicts: Tuple[StructuralConflict, ...]) -> ConflictInboxItem:
        return ConflictInboxItem(case_id, conflicts, sum(c.predicted_heat for c in conflicts))
