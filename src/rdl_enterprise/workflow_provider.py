"""HTTP adapter for a read-only workflow provider."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .canary import ActionCapability
from .tool_execution import ToolSpec


class WorkflowProviderError(RuntimeError):
    """The external workflow provider returned an unusable response."""


class WorkflowProviderAuthError(WorkflowProviderError):
    """The provider rejected the supplied credentials or scope."""


class WorkflowProviderNotFoundError(WorkflowProviderError):
    """The requested workflow case does not exist at the provider."""


class WorkflowProviderUnavailableError(WorkflowProviderError):
    """The provider could not be reached or returned a server failure."""


class WorkflowHttpConnector:
    """Translate a workflow provider's JSON lookup endpoint into a ToolSpec."""

    def __init__(
        self,
        base_url: str,
        *,
        opener: Optional[Callable[..., Any]] = None,
        timeout: float = 5.0,
        api_token: Optional[str] = None,
    ):
        if not base_url.strip():
            raise ValueError("base_url is required")
        self.base_url = base_url.rstrip("/")
        self._opener = opener or urlopen
        self.timeout = timeout
        self.api_token = api_token

    def lookup(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        case_id = payload.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("case_id is required")
        headers = {"Accept": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        request = Request(
            f"{self.base_url}/cases/{quote(case_id, safe='')}",
            headers=headers,
            method="GET",
        )
        try:
            response = self._opener(request, timeout=self.timeout)
            raw = response.read()
            body = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise WorkflowProviderAuthError("workflow provider rejected credentials or scope") from exc
            if exc.code == 404:
                raise WorkflowProviderNotFoundError("workflow case was not found") from exc
            if exc.code >= 500:
                raise WorkflowProviderUnavailableError("workflow provider returned a server error") from exc
            raise WorkflowProviderError(f"workflow provider returned HTTP {exc.code}") from exc
        except (TimeoutError, URLError) as exc:
            raise WorkflowProviderUnavailableError("workflow provider is unavailable") from exc
        except Exception as exc:
            raise WorkflowProviderError("workflow provider lookup failed") from exc
        if not isinstance(body, Mapping):
            raise WorkflowProviderError("workflow provider returned a non-object response")
        required = ("case_id", "status", "owner", "summary")
        if any(not isinstance(body.get(field), str) for field in required):
            raise WorkflowProviderError("workflow provider response has an invalid schema")
        if body["case_id"] != case_id:
            raise WorkflowProviderError("workflow provider response does not match requested case")
        return {field: body[field] for field in required}

    def tool_spec(self) -> ToolSpec:
        return ToolSpec(
            tool_id="workflow.provider.lookup",
            domain="workflow",
            capability=ActionCapability.DRY_RUN_ONLY,
            handler=self.lookup,
        )
