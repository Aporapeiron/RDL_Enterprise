"""Small workflow-system connector used by the first business vertical slice."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping

from .canary import ActionCapability
from .tool_execution import ToolSpec


@dataclass(frozen=True)
class WorkflowCase:
    case_id: str
    status: str
    owner: str
    summary: str


class WorkflowConnector:
    """Read-only adapter for a finite workflow-system fixture or client."""

    def __init__(self, cases: Mapping[str, WorkflowCase]):
        self._cases = dict(cases)

    def lookup(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        case_id = payload.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("case_id is required")
        case = self._cases.get(case_id)
        if case is None:
            return {"found": False, "case_id": case_id}
        return {
            "found": True,
            "case_id": case.case_id,
            "status": case.status,
            "owner": case.owner,
            "summary": case.summary,
        }

    def tool_spec(self) -> ToolSpec:
        return ToolSpec(
            tool_id="workflow.lookup",
            domain="workflow",
            capability=ActionCapability.DRY_RUN_ONLY,
            handler=self.lookup,
        )
