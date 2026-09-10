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
                 payload: Dict[str, Any], actor: AuthorityContext, ticket_id: str,
                 operation_id: str = "", allow_irreversible: bool = False) -> ToolExecutionResult:
    service._require_authenticated(actor)
    spec = registry.get(tool_id)
    service._require_scope(actor, spec.domain)
    if spec.capability == ActionCapability.IRREVERSIBLE and not allow_irreversible:
        raise AuthorizationError("irreversible tool execution requires explicit approval")
    for existing in service.runtime.canary_manager.action_ledger.records:
        if getattr(existing, "operation_id", None) == operation_id and operation_id:
            if existing.ticket_id != ticket_id or existing.action_type != f"tool:{tool_id}":
                raise ValueError("operation_id is bound to another tool operation")
            return ToolExecutionResult(tool_id, existing.action_id, existing.compensation_result.get("output") if existing.compensation_result else None)
    
    record = service.runtime.canary_manager.action_ledger.record_action(
        ticket_id=ticket_id,
        mb_version=getattr(service.runtime.mb_graph, "version", "unknown"),
        is_canary=False,
        action_type=f"tool:{tool_id}",
        payload=payload,
        capability=spec.capability,
    )
    record.operation_id = operation_id
    record.status = "planned"
    if service.runtime.case_store:
        service.runtime._persist_runtime_state()
    try:
        output = spec.handler(payload)
        record.status = "succeeded"
        record.compensation_result = {"output": output}
    except Exception as exc:
        record.status = "failed"
        record.compensation_result = {"error": str(exc)}
        if service.runtime.case_store:
            service.runtime._persist_runtime_state()
        raise
    if service.runtime.case_store:
        service.runtime._persist_runtime_state()
    return ToolExecutionResult(tool_id, record.action_id, output)
