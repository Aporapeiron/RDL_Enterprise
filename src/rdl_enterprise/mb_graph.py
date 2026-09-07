import hashlib
import json
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Dict, List, Any

@dataclass
class MBNode:
    id: str
    domain: str
    trigger_pattern: Dict[str, Any]  # {"exact_keys": [...], "rule_expr": "...", "embedding": [...]}
    action_template: Dict[str, Any]  # {"type": "direct_reply"|"tool_call"|"ask_human"|"delegate", "payload": ...}
    authority_level: str = "auto"    # "auto" | "require_approval" | "human_only"
    confidence: float = 0.5
    success_count: int = 0
    failure_count: int = 0
    approval_count: int = 0
    rejection_count: int = 0
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    last_updated: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def inertia(self) -> float:
        """
        整合慣性質量 ||M_B||(node)
        ||M_B|| = max(0.0, confidence * (1 + 0.3*success + 0.5*approval - 0.5*failure - 0.8*rejection))
        """
        val = self.confidence * (
            1.0
            + 0.3 * self.success_count
            + 0.5 * self.approval_count
            - 0.5 * self.failure_count
            - 0.8 * self.rejection_count
        )
        return max(0.0, float(val))

    def kappa(self, m0: float = 3.0) -> float:
        """
        自己修正可能性 κ(node) = exp(-||M_B|| / M0) ∈ (0, 1]
        """
        return math.exp(-self.inertia() / m0)

    def record_success(self, approved: bool = False):
        self.success_count += 1
        if approved:
            self.approval_count += 1
        self.confidence = min(1.0, self.confidence + 0.05)
        self.last_updated = datetime.utcnow().isoformat()

    def record_failure(self, rejected: bool = False):
        self.failure_count += 1
        if rejected:
            self.rejection_count += 1
        self.confidence = max(0.1, self.confidence - 0.1)
        self.last_updated = datetime.utcnow().isoformat()


class MBGraph:
    def __init__(self, m0: float = 3.0, version: str = "v1.0", is_frozen: bool = False):
        self.nodes: Dict[str, MBNode] = {}
        self.m0 = m0
        self.version = version
        self.is_frozen = is_frozen

    def freeze(self):
        """候補グラフを完全固定（Immutable化: Canary中のIdentity Drift防止）"""
        self.is_frozen = True

    def unfreeze(self):
        """凍結解除"""
        self.is_frozen = False

    def content_hash(self) -> str:
        """
        グラフの論理的実体（ノード構造、ルール、アクション定義）に対する暗号論的ハッシュ (SHA-256)
        Durability / Shadow 検査時の対象と、最終昇格時の対象が完全一致することを保証する。
        """
        canonical_nodes = []
        for nid in sorted(self.nodes.keys()):
            node = self.nodes[nid]
            canonical_nodes.append({
                "id": node.id,
                "domain": node.domain,
                "trigger_pattern": node.trigger_pattern,
                "action_template": node.action_template,
                "authority_level": node.authority_level,
                "confidence": round(node.confidence, 4),
            })
        payload = {
            "version": self.version,
            "nodes": canonical_nodes,
        }
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def add_or_update(self, node: MBNode):
        if self.is_frozen:
            raise RuntimeError(f"MBGraph (version={self.version}) は凍結(frozen)されています。ノード {node.id} の変更・追加は禁止されています。")
        self.nodes[node.id] = node

    def get(self, node_id: str) -> Optional[MBNode]:
        return self.nodes.get(node_id)

    def remove(self, node_id: str) -> bool:
        if self.is_frozen:
            raise RuntimeError(f"MBGraph (version={self.version}) は凍結(frozen)されています。ノード {node_id} の削除は禁止されています。")
        if node_id in self.nodes:
            del self.nodes[node_id]
            return True
        return False

    def list_nodes(self, domain: Optional[str] = None) -> List[MBNode]:
        if domain:
            return [n for n in self.nodes.values() if n.domain == domain]
        return list(self.nodes.values())

    def total_inertia(self) -> float:
        return sum(n.inertia() for n in self.nodes.values())

    def average_kappa(self) -> float:
        if not self.nodes:
            return 1.0
        return sum(n.kappa(self.m0) for n in self.nodes.values()) / len(self.nodes)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "m0": self.m0,
            "version": self.version,
            "is_frozen": self.is_frozen,
            "content_hash": self.content_hash(),
            "nodes": {nid: asdict(node) for nid, node in self.nodes.items()}
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MBGraph":
        graph = cls(
            m0=data.get("m0", 3.0),
            version=data.get("version", "v1.0"),
            is_frozen=data.get("is_frozen", False),
        )
        for nid, ndict in data.get("nodes", {}).items():
            graph.nodes[nid] = MBNode(**ndict)
        return graph

    def save_json(self, filepath: str):
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load_json(cls, filepath: str) -> "MBGraph":
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
