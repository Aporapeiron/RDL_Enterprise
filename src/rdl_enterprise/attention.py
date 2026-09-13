"""Enterprise-local review aggregation; no notification transport or H update."""

from dataclasses import dataclass, replace

from .authority import AuthorityContext


@dataclass(frozen=True)
class ReviewRequest:
    case_id: str
    domain: str
    actor_id: str
    change_points: tuple[str, ...]


class HumanAttentionGate:
    def __init__(self):
        self._requests: dict[tuple[str, str], ReviewRequest] = {}

    @property
    def attention_load(self) -> int:
        return len(self._requests)

    def requests(self) -> tuple[ReviewRequest, ...]:
        return tuple(self._requests.values())

    def export_state(self) -> tuple[ReviewRequest, ...]:
        return self.requests()

    def restore_state(self, requests: tuple[ReviewRequest, ...] | list[ReviewRequest]) -> None:
        for request in requests:
            if not isinstance(request, ReviewRequest):
                raise ValueError("invalid review request state")
            key = (request.case_id, request.domain)
            existing = self._requests.get(key)
            if existing is not None and existing.actor_id != request.actor_id:
                raise ValueError("review is already assigned to another actor")
            self._requests[key] = request

    def consider(self, *, case_id: str, domain: str, change_point: str,
                 actor: AuthorityContext, actionable: bool,
                 persistent: bool, safety_required: bool = False) -> ReviewRequest | None:
        if not case_id or not domain or not change_point:
            raise ValueError("case, domain and change point are required")
        if not (actionable is True and
                (persistent is True or safety_required is True)):
            return None
        if not (actor.actor_id and actor.is_human_authenticated()
                and actor.is_authorized_for(domain)):
            return None
        key = (case_id, domain)
        previous = self._requests.get(key)
        if previous is not None:
            if previous.actor_id != actor.actor_id:
                raise ValueError("review is already assigned to another actor")
            if change_point not in previous.change_points:
                previous = replace(previous, change_points=previous.change_points + (change_point,))
                self._requests[key] = previous
            return previous
        request = ReviewRequest(case_id, domain, actor.actor_id, (change_point,))
        self._requests[key] = request
        return request
