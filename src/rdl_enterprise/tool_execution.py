"""Enterprise-local tool execution boundary with explicit capability checks."""

from dataclasses import dataclass
from typing import Any, Callable, Dict

from .authority import AuthorityContext
from .canary import ActionCapability
from .service import AuthorizationError, AuthenticationError, EnterpriseService


@dataclass(frozen=True)
class ToolSpec:
    tool_id: str
    domain: str
    capability: ActionCapability = ActionCapability.DRY_RUN_ONLY
    handler: Callable[[Dict[str, Any]], Any] = lambda payload: payload


@dataclass(frozen=True)
class ToolExecutionResult:
    tool_id: str
    action_id: str
    output: Any


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if not spec.tool_id.strip():
            raise ValueError("tool_id is required")
        if spec.tool_id in self._tools:
            raise ValueError(f"tool already registered: {spec.tool_id}")
        self._tools[spec.tool_id] = spec

    def get(self, tool_id: str) -> ToolSpec:
        try:
            return self._tools[tool_id]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {tool_id}") from exc


def execute_tool(service: EnterpriseService, registry: ToolRegistry, tool_id: str,
                 payload: Dict[str, Any], actor: AuthorityContext, ticket_id: str) -> ToolExecutionResult:
    service._require_authenticated(actor)
    spec = registry.get(tool_id)
    service._require_scope(actor, spec.domain)
    output = spec.handler(payload)
    record = service.runtime.canary_manager.action_ledger.record_action(
        ticket_id=ticket_id,
        mb_version=getattr(service.runtime.mb_graph, "version", "unknown"),
        is_canary=False,
        action_type=f"tool:{tool_id}",
        payload=payload,
        capability=spec.capability,
    )
    if service.runtime.case_store:
        service.runtime._persist_runtime_state()
    return ToolExecutionResult(tool_id, record.action_id, output)
