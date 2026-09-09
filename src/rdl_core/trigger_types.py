"""Minimal, explicit trigger description and matching observations."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple
import re

from .contracts import BoundaryContext, Provenance
from .function_types import FunctionDescription


class MatchingObservationStatus(str, Enum):
    MATCHED = "matched"
    NOT_MATCHED = "not_matched"
    UNRESOLVED = "unresolved"


def normalize_exact_key_text(text: str) -> str:
    """Normalize exact-key text using the bounded Enterprise matching policy."""
    if not isinstance(text, str):
        raise TypeError("exact-key textは文字列である必要があります")
    return re.sub(r"\s+", "", text.casefold())


def exact_key_matches(key: str, query: str) -> bool:
    """Apply normalized equality or case-insensitive containment."""
    if not isinstance(key, str) or not isinstance(query, str):
        raise TypeError("exact-key matchingの入力は文字列である必要があります")
    return normalize_exact_key_text(key) == normalize_exact_key_text(query) or key.casefold() in query.casefold()


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
    evaluator: Optional[FunctionDescription] = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, MatchingObservationStatus):
            raise TypeError("statusはMatchingObservationStatusである必要があります")
        if not isinstance(self.query, str):
            raise TypeError("queryは文字列である必要があります")
        if not isinstance(self.evaluator_id, str) or not isinstance(self.evaluator_version, str):
            raise TypeError("evaluator identity/versionは文字列である必要があります")
        if not self.evaluator_id.strip() or not self.evaluator_version.strip():
            raise ValueError("evaluator identity/versionは空にできません")
        evaluator = self.evaluator or FunctionDescription(self.evaluator_id, self.evaluator_version)
        if (evaluator.function_id, evaluator.version) != (self.evaluator_id, self.evaluator_version):
            raise ValueError("evaluatorとevaluator_id/versionが一致していません")
        object.__setattr__(self, "evaluator", evaluator)
        matched_keys = tuple(self.matched_keys)
        if any(not isinstance(key, str) for key in matched_keys):
            raise TypeError("matched_keysは文字列列である必要があります")
        if any(key not in self.trigger.exact_keys for key in matched_keys):
            raise ValueError("matched_keysはTriggerDescriptionのexact_keysに含まれる必要があります")
        object.__setattr__(self, "matched_keys", matched_keys)


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
        matched = tuple(key for key in trigger.exact_keys if exact_key_matches(key, query))
        status = MatchingObservationStatus.MATCHED if matched else MatchingObservationStatus.NOT_MATCHED
    return MatchingObservation(trigger, query, status, matched, context, provenance=provenance)
