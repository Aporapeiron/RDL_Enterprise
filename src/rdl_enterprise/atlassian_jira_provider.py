"""Read-only Atlassian Jira/JSM provider adapter."""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Callable, Dict, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .canary import ActionCapability
from .tool_execution import ToolSpec


class AtlassianProviderError(RuntimeError):
    """A bounded Jira provider failure without credential material."""


class AtlassianProviderAuthError(AtlassianProviderError):
    pass


class AtlassianProviderNotFoundError(AtlassianProviderError):
    pass


class AtlassianProviderUnavailableError(AtlassianProviderError):
    pass


class AtlassianJiraConnector:
    """Project a Jira issue response into the RDL business-case boundary."""

    def __init__(
        self,
        base_url: str,
        email: str,
        token: str,
        *,
        opener: Optional[Callable[..., Any]] = None,
        timeout: float = 5.0,
    ):
        if not base_url.strip() or not email.strip() or not token:
            raise ValueError("base_url, email and token are required")
        self.base_url = base_url.rstrip("/")
        self._opener = opener or urlopen
        self.timeout = timeout
        self._authorization = "Basic " + base64.b64encode(
            f"{email}:{token}".encode("utf-8")
        ).decode("ascii")

    @classmethod
    def from_environment(
        cls, *, opener: Optional[Callable[..., Any]] = None, timeout: float = 5.0
    ) -> "AtlassianJiraConnector":
        return cls(
            os.environ.get("RDL_ATLASSIAN_BASE_URL", ""),
            os.environ.get("RDL_ATLASSIAN_EMAIL", ""),
            os.environ.get("RDL_ATLASSIAN_TOKEN", ""),
            opener=opener,
            timeout=timeout,
        )

    def lookup(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        case_id = payload.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("case_id is required")
        path = f"/rest/api/3/issue/{quote(case_id, safe='')}?fields=summary,status,assignee"
        request = Request(
            f"{self.base_url}{path}",
            headers={
                "Accept": "application/json",
                "Authorization": self._authorization,
            },
            method="GET",
        )
        try:
            response = self._opener(request, timeout=self.timeout)
            raw = response.read()
            body = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise AtlassianProviderAuthError("Atlassian credentials or scope rejected") from exc
            if exc.code == 404:
                raise AtlassianProviderNotFoundError("Jira issue was not found") from exc
            if exc.code >= 500:
                raise AtlassianProviderUnavailableError("Atlassian provider unavailable") from exc
            raise AtlassianProviderError(f"Atlassian provider returned HTTP {exc.code}") from exc
        except (TimeoutError, URLError) as exc:
            raise AtlassianProviderUnavailableError("Atlassian provider unavailable") from exc
        except Exception as exc:
            raise AtlassianProviderError("Atlassian response could not be decoded") from exc

        if not isinstance(body, Mapping):
            raise AtlassianProviderError("Atlassian response is not an object")
        fields = body.get("fields")
        if not isinstance(fields, Mapping):
            raise AtlassianProviderError("Atlassian issue fields are missing")
        summary = fields.get("summary")
        status = fields.get("status")
        assignee = fields.get("assignee")
        if not isinstance(body.get("key"), str):
            raise AtlassianProviderError("Atlassian issue key is missing")
        if not isinstance(summary, str) or not isinstance(status, Mapping):
            raise AtlassianProviderError("Atlassian issue response has an invalid schema")
        status_name = status.get("name")
        if not isinstance(status_name, str):
            raise AtlassianProviderError("Atlassian issue status is missing")
        if assignee is None:
            owner: Optional[str] = None
        elif isinstance(assignee, Mapping):
            owner = assignee.get("displayName")
            if owner is not None and not isinstance(owner, str):
                raise AtlassianProviderError("Atlassian assignee has an invalid schema")
        else:
            raise AtlassianProviderError("Atlassian assignee has an invalid schema")
        if body["key"] != case_id:
            raise AtlassianProviderError("Atlassian response does not match requested issue")
        return {"case_id": body["key"], "summary": summary, "status": status_name, "owner": owner}

    def tool_spec(self) -> ToolSpec:
        return ToolSpec(
            tool_id="atlassian.jira.issue.lookup",
            domain="workflow",
            capability=ActionCapability.DRY_RUN_ONLY,
            handler=self.lookup,
        )
