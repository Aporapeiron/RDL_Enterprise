"""Minimal, explicit trigger description and matching observations."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from .contracts import BoundaryContext, Provenance


class MatchingObservationStatus(str, Enum):
    MATCHED = "matched"
    NOT_MATCHED = "not_matched"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class TriggerDescription:
    """Description of exact-key candidates; not a policy or Commitment."""

    exact_keys: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        keys = tuple(self.exact_keys)
        if any(not isinstance(key, str) or not key.strip() for key in keys):
            raise ValueError("exact_keysは非空文字列のtupleである必要があります")
        object.__setattr__(self, "exact_keys", keys)


@dataclass(frozen=True)
class MatchingObservation:
    trigger: TriggerDescription
    query: str
    status: MatchingObservationStatus
    matched_keys: Tuple[str, ...]
    context: BoundaryContext
    evaluator_id: str = "rdl_core.exact_keys"
    evaluator_version: str = "0"
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, MatchingObservationStatus):
            raise TypeError("statusはMatchingObservationStatusである必要があります")
        if not isinstance(self.query, str):
            raise TypeError("queryは文字列である必要があります")
        if not isinstance(self.evaluator_id, str) or not isinstance(self.evaluator_version, str):
            raise TypeError("evaluator identity/versionは文字列である必要があります")
        if not self.evaluator_id.strip() or not self.evaluator_version.strip():
            raise ValueError("evaluator identity/versionは空にできません")
        if any(key not in self.trigger.exact_keys for key in self.matched_keys):
            raise ValueError("matched_keysはTriggerDescriptionのexact_keysに含まれる必要があります")


def observe_exact_keys(
    trigger: TriggerDescription,
    query: str,
    context: BoundaryContext,
    *,
    provenance: Optional[Provenance] = None,
) -> MatchingObservation:
    """Observe exact-key matching without inferring truth or polarity."""
    if not isinstance(query, str):
        raise TypeError("queryは文字列である必要があります")
    if not trigger.exact_keys:
        status = MatchingObservationStatus.UNRESOLVED
        matched = ()
    else:
        matched = tuple(key for key in trigger.exact_keys if key in query)
        status = MatchingObservationStatus.MATCHED if matched else MatchingObservationStatus.NOT_MATCHED
    return MatchingObservation(trigger, query, status, matched, context, provenance=provenance)
