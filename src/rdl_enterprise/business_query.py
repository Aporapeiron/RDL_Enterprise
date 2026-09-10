"""Product entry point for read-only natural-language business queries."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .atlassian_jira_provider import (
    AtlassianProviderAuthError,
    AtlassianProviderNotFoundError,
    AtlassianProviderUnavailableError,
)
from .authority import AuthorityContext
from .service import AuthorizationError, AuthenticationError, EnterpriseService
from .tool_execution import ToolRegistry, execute_tool
from .tool_routing import route_business_text


def handle_business_query(
    service: EnterpriseService,
    registry: ToolRegistry,
    text: str,
    actor: AuthorityContext,
    *,
    ticket_id: str = "business-query",
    operation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Route and execute one read-only query, returning bounded operator output."""
    routing = route_business_text(text)
    result: Dict[str, Any] = {"routing_status": routing.status.value}
    if routing.reason:
        result["reason"] = routing.reason
    candidate = routing.candidate
    if candidate is None:
        return result
    if not candidate.read_only:
        result["routing_status"] = "UNRESOLVED"
        result["reason"] = "non-read-only candidate is outside this entry point"
        return result
    try:
        execution = execute_tool(
            service,
            registry,
            candidate.tool_id,
            candidate.payload,
            actor,
            ticket_id,
            operation_id or "",
        )
    except (AuthenticationError, AuthorizationError):
        result["routing_status"] = "AUTHORIZATION_REJECTED"
        result["reason"] = "actor is not authorized for this read-only query"
        return result
    except AtlassianProviderAuthError:
        result["routing_status"] = "PROVIDER_AUTH_ERROR"
        return result
    except AtlassianProviderNotFoundError:
        result["routing_status"] = "PROVIDER_NOT_FOUND"
        return result
    except AtlassianProviderUnavailableError:
        result["routing_status"] = "PROVIDER_UNAVAILABLE"
        return result

    output = execution.output
    if not isinstance(output, dict):
        result["routing_status"] = "UNKNOWN"
        result["reason"] = "provider result was not a bounded object"
        return result
    result.update({
        "case_id": output.get("case_id"),
        "summary": output.get("summary"),
        "status": output.get("status"),
        "owner": output.get("owner"),
        "source": "atlassian_jira",
    })
    return result
