"""Small, Enterprise-independent RDL semantic contracts."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class EvidencePolarity(str, Enum):
    SUPPORT = "support"
    OPPOSE = "oppose"
    UNRESOLVED = "unresolved"


class CommitmentOrigin(str, Enum):
    AUTHORITY = "authority"
    VERIFIED_EXPERIENCE = "experience"
    AUTHORITATIVE_SEED = "seed"
    PROMOTION = "promotion"
    MIGRATION_VERIFIED = "migration"
    TEST_FIXTURE = "test_fixture"


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
    conditions: Dict[str, Any] = field(default_factory=dict)


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

        valid_origins = {item.value for item in CommitmentOrigin}
        if origin not in valid_origins:
            raise ValueError(f"無効または未知の commitment origin: '{origin}'。有効値: {valid_origins}")

        for field_name in ("committed_at", "evidence_at"):
            value = rec_dict.get(field_name)
            if value is not None:
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
