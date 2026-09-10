"""Small T2 tool for checking joint action feasibility.

This module deliberately does not decide promotion or mutate a model.  It
only compares finite action effects with finite required effects.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Tuple

from rdl_core import BoundaryContext, Provenance


class ActionFeasibilityStatus(str, Enum):
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class ActionFeasibility:
    action_id: str
    status: ActionFeasibilityStatus
    violated: Tuple[str, ...] = ()
    unresolved: Tuple[str, ...] = ()


@dataclass(frozen=True)
class JointActionInspection:
    actions: Tuple[ActionFeasibility, ...]
    status: ActionFeasibilityStatus
    context: BoundaryContext | None = None
    provenance: Provenance | None = None


def inspect_joint_action_feasibility(
    actions: Mapping[str, Mapping[str, bool]],
    requirements: Mapping[str, bool],
    *,
    context: BoundaryContext | None = None,
    provenance: Provenance | None = None,
) -> JointActionInspection:
    """Inspect whether any finite action satisfies all requirements.

    Missing action effects remain unresolved; they are never treated as
    ``False``.  The result is infeasible only when every action is fully
    evaluated and each one violates at least one requirement.
    """
    results = []
    for action_id, effects in actions.items():
        violated = []
        unresolved = []
        for key, expected in requirements.items():
            if key not in effects:
                unresolved.append(key)
            elif effects[key] is not expected:
                violated.append(key)

        if unresolved:
            status = ActionFeasibilityStatus.UNRESOLVED
        elif violated:
            status = ActionFeasibilityStatus.INFEASIBLE
        else:
            status = ActionFeasibilityStatus.FEASIBLE
        results.append(ActionFeasibility(action_id, status, tuple(violated), tuple(unresolved)))

    inspections = tuple(results)
    if not inspections:
        # No candidate was expanded or inspected; absence is not proof of
        # infeasibility.
        overall = ActionFeasibilityStatus.UNRESOLVED
    elif any(item.status is ActionFeasibilityStatus.FEASIBLE for item in inspections):
        overall = ActionFeasibilityStatus.FEASIBLE
    elif any(item.status is ActionFeasibilityStatus.UNRESOLVED for item in inspections):
        overall = ActionFeasibilityStatus.UNRESOLVED
    else:
        overall = ActionFeasibilityStatus.INFEASIBLE
    return JointActionInspection(inspections, overall, context, provenance)
