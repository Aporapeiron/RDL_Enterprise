"""Application boundary for authenticated Enterprise business operations."""

from __future__ import annotations

from typing import Optional

from .authority import AuthorityContext
from .runtime import EnterpriseRuntime, TicketDispatchResult, TicketResolutionResult
from .snapshot import BusinessInput, FeedbackResult


class AuthenticationError(ValueError):
    """The caller did not provide an authenticated operational identity."""


class AuthorizationError(PermissionError):
    """The authenticated actor is outside the requested business scope."""


class EnterpriseService:
    """Small service facade; policy and metabolism remain owned by Runtime."""

    def __init__(self, runtime: EnterpriseRuntime):
        self.runtime = runtime

    @staticmethod
    def _require_authenticated(actor: AuthorityContext) -> None:
        trusted_methods = {"idp_sso", "mfa", "console", "passkey", "api_key", "delegated_agent"}
        if not actor.actor_id.strip() or actor.authenticated_by not in trusted_methods:
            raise AuthenticationError("authenticated actor is required")
        if actor.actor_type not in ("human", "service", "agent"):
            raise AuthenticationError("unsupported actor type")

    @staticmethod
    def _require_scope(actor: AuthorityContext, domain: str) -> None:
        if actor.scope not in ("all", "*", domain):
            raise AuthorizationError(f"actor is not authorized for domain '{domain}'")

    def submit(self, request: BusinessInput, actor: AuthorityContext) -> TicketDispatchResult:
        self._require_authenticated(actor)
        if not request.ticket_id.strip() or not request.category.strip():
            raise ValueError("ticket_id and category are required")
        self._require_scope(actor, request.category)
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
        snapshot = self.runtime.pending_snapshots.get(ticket_id)
        if snapshot is not None:
            self._require_scope(actor, snapshot.efp.category)
        return self.runtime.resolve_ticket_feedback(ticket_id, feedback, operation_id=operation_id)
