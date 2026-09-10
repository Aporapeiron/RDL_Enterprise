"""Minimal natural-language routing to read-only tool candidates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Optional


class ToolRoutingStatus(str, Enum):
    RESOLVED = "RESOLVED"
    UNKNOWN = "UNKNOWN"
    UNRESOLVED = "UNRESOLVED"
    NOT_EVALUATED = "NOT_EVALUATED"


@dataclass(frozen=True)
class ToolCandidate:
    tool_id: str
    payload: dict[str, str]
    read_only: bool = True


@dataclass(frozen=True)
class ToolRoutingResult:
    status: ToolRoutingStatus
    candidate: Optional[ToolCandidate] = None
    reason: str = ""


_ISSUE_KEY = re.compile(r"(?<![A-Z0-9])([A-Z][A-Z0-9]+-\d+)(?![A-Z0-9])")


def route_business_text(text: str) -> ToolRoutingResult:
    """Resolve an explicit Jira issue key, without executing or asserting truth."""
    if not isinstance(text, str) or not text.strip():
        return ToolRoutingResult(ToolRoutingStatus.NOT_EVALUATED, reason="business text is empty")
    keys = tuple(dict.fromkeys(_ISSUE_KEY.findall(text.upper())))
    if len(keys) > 1:
        return ToolRoutingResult(ToolRoutingStatus.UNRESOLVED, reason="multiple issue keys were observed")
    if len(keys) == 1:
        return ToolRoutingResult(
            ToolRoutingStatus.RESOLVED,
            ToolCandidate("atlassian.jira.issue.lookup", {"case_id": keys[0]}, read_only=True),
        )
    return ToolRoutingResult(ToolRoutingStatus.UNRESOLVED, reason="unique issue key was not observed")
