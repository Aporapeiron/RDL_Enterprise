"""Small, Enterprise-independent RDL semantic contracts."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional, Union


class EvidencePolarity(str, Enum):
    SUPPORT = "support"
    OPPOSE = "oppose"
    UNRESOLVED = "unresolved"


class CommitmentOrigin(str, Enum):
    """Canonical origins; domain policies may add namespaced origin kinds."""
    AUTHORITY = "authority"
    VERIFIED_EXPERIENCE = "experience"
    AUTHORITATIVE_SEED = "seed"
    PROMOTION = "promotion"
    MIGRATION_VERIFIED = "migration"
    TEST_FIXTURE = "test_fixture"


BoundaryValue = Union[None, bool, int, float, str, tuple]


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_deep_freeze(item) for item in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"BoundaryContext.conditions に対応しない値型です: {type(value).__name__}")


def _valid_origin(origin: str) -> bool:
    canonical = {item.value for item in CommitmentOrigin}
    # Domain-specific taxonomies use a recoverable namespace, e.g. game:rumor.
    return origin in canonical or (":" in origin and all(part.strip() for part in origin.split(":", 1)))


@dataclass(frozen=True)
class Provenance:
    source: str
    actor: Optional[str] = None
    observed_at: Optional[str] = None
    authority_ref: Optional[str] = None
    lineage: Optional[str] = None


@dataclass(frozen=True)
class BoundaryContext:
    boundary_id: str
    question: Optional[str] = None
    observation_time: Optional[str] = None
    purpose: Optional[str] = None
    conditions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "conditions", _deep_freeze(dict(self.conditions)))


@dataclass(frozen=True)
class AuthorityConstraint:
    authority_ref: str
    scope: str
    target: str
    relation: str


@dataclass(frozen=True)
class CommitmentRecord:
    origin: str
    committed_at: str
    actor: str
    evidence_at: Optional[str] = None
    lineage: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "origin": self.origin,
            "committed_at": self.committed_at,
            "actor": self.actor,
            "evidence_at": self.evidence_at,
            "lineage": self.lineage,
        }

    @classmethod
    def from_dict_strict(
        cls,
        rec_dict: Any,
        outer_origin: Optional[str] = None,
        outer_committed_at: Optional[str] = None,
    ) -> "CommitmentRecord":
        if not isinstance(rec_dict, dict):
            raise ValueError(f"commitment_record は辞書型である必要があります: {type(rec_dict)}")

        origin = rec_dict.get("origin")
        committed_at = rec_dict.get("committed_at")
        actor = rec_dict.get("actor")
        if not isinstance(origin, str) or not origin.strip():
            raise ValueError("commitment_record.origin は必須の非空文字列です")
        if not isinstance(committed_at, str) or not committed_at.strip():
            raise ValueError("commitment_record.committed_at は必須の非空文字列です")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("commitment_record.actor は必須の非空文字列です")

        if not _valid_origin(origin):
            valid_origins = sorted(item.value for item in CommitmentOrigin)
            raise ValueError(f"無効または名前空間のない commitment origin: '{origin}'。標準値: {valid_origins}")

        for field_name in ("committed_at", "evidence_at"):
            value = rec_dict.get(field_name)
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"commitment_record.{field_name} は非空の文字列である必要があります")
                try:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                except (ValueError, TypeError) as exc:
                    raise ValueError(f"commitment_record.{field_name} は有効な ISO-8601 時刻文字列である必要があります: '{value}' ({exc})") from exc

        if outer_origin is not None and outer_origin != origin:
            raise ValueError("commitment_record の origin と外側フィールドが不一致です")
        if outer_committed_at is not None and outer_committed_at != committed_at:
            raise ValueError("commitment_record の committed_at と外側フィールドが不一致です")

        return cls(
            origin=origin,
            committed_at=committed_at,
            actor=actor,
            evidence_at=rec_dict.get("evidence_at"),
            lineage=rec_dict.get("lineage"),
        )
