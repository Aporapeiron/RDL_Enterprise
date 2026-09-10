"""Application boundary for authenticated Enterprise business operations."""

from __future__ import annotations

from typing import Optional

from .authority import AuthorityContext
from .runtime import EnterpriseRuntime, TicketDispatchResult, TicketResolutionResult
from .snapshot import BusinessInput, FeedbackResult


class AuthenticationError(ValueError):
    """The caller did not provide an authenticated operational identity."""


class EnterpriseService:
    """Small service facade; policy and metabolism remain owned by Runtime."""

    def __init__(self, runtime: EnterpriseRuntime):
        self.runtime = runtime

    @staticmethod
    def _require_authenticated(actor: AuthorityContext) -> None:
        if not actor.actor_id.strip() or not actor.authenticated_by:
            raise AuthenticationError("authenticated actor is required")
        if actor.actor_type not in ("human", "service", "agent"):
            raise AuthenticationError("unsupported actor type")

    def submit(self, request: BusinessInput, actor: AuthorityContext) -> TicketDispatchResult:
        self._require_authenticated(actor)
        if not request.ticket_id.strip() or not request.category.strip():
            raise ValueError("ticket_id and category are required")
        return self.runtime.dispatch_ticket(request, authority=actor)

    def record_feedback(
        self,
        ticket_id: str,
        feedback: FeedbackResult,
        actor: AuthorityContext,
        operation_id: Optional[str] = None,
    ) -> TicketResolutionResult:
        self._require_authenticated(actor)
        if not ticket_id.strip():
            raise ValueError("ticket_id is required")
        return self.runtime.resolve_ticket_feedback(ticket_id, feedback, operation_id=operation_id)
