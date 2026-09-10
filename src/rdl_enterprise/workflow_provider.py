"""HTTP adapter for a read-only workflow provider."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Mapping, Optional
from urllib.request import Request, urlopen

from .canary import ActionCapability
from .tool_execution import ToolSpec


class WorkflowProviderError(RuntimeError):
    """The external workflow provider returned an unusable response."""


class WorkflowHttpConnector:
    """Translate a workflow provider's JSON lookup endpoint into a ToolSpec."""

    def __init__(
        self,
        base_url: str,
        *,
        opener: Optional[Callable[..., Any]] = None,
        timeout: float = 5.0,
    ):
        if not base_url.strip():
            raise ValueError("base_url is required")
        self.base_url = base_url.rstrip("/")
        self._opener = opener or urlopen
        self.timeout = timeout

    def lookup(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        case_id = payload.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("case_id is required")
        request = Request(
            f"{self.base_url}/cases/{case_id}",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            response = self._opener(request, timeout=self.timeout)
            raw = response.read()
            body = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        except Exception as exc:
            raise WorkflowProviderError("workflow provider lookup failed") from exc
        if not isinstance(body, Mapping):
            raise WorkflowProviderError("workflow provider returned a non-object response")
        return dict(body)

    def tool_spec(self) -> ToolSpec:
        return ToolSpec(
            tool_id="workflow.provider.lookup",
            domain="workflow",
            capability=ActionCapability.DRY_RUN_ONLY,
            handler=self.lookup,
        )
