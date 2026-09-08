import hashlib
import json
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Dict, List, Any

class ReadOnlyDict(dict):
    """凍結ノード内部の辞書不変性を担保する読み取り専用辞書"""
    def __copy__(self):
        return ReadOnlyDict(self)

    def __deepcopy__(self, memo):
        import copy
        return ReadOnlyDict({copy.deepcopy(k, memo): copy.deepcopy(v, memo) for k, v in self.items()})

    def __setitem__(self, key, value):
        raise TypeError(f"ReadOnlyDict は凍結されており変更できません (キー: {key})")

    def __delitem__(self, key):
        raise TypeError(f"ReadOnlyDict は凍結されており変更できません (キー: {key})")

    def pop(self, *args, **kwargs):
        raise TypeError("ReadOnlyDict は凍結されており変更できません")

    def popitem(self, *args, **kwargs):
        raise TypeError("ReadOnlyDict は凍結されており変更できません")

    def clear(self):
        raise TypeError("ReadOnlyDict は凍結されており変更できません")

    def update(self, *args, **kwargs):
        raise TypeError("ReadOnlyDict は凍結されており変更できません")

    def setdefault(self, *args, **kwargs):
        raise TypeError("ReadOnlyDict は凍結されており変更できません")


class ReadOnlyList(list):
    """凍結ノード内部のリスト不変性を担保する読み取り専用リスト"""
    def __copy__(self):
        return ReadOnlyList(self)

    def __deepcopy__(self, memo):
        import copy
        return ReadOnlyList([copy.deepcopy(x, memo) for x in self])

    def __setitem__(self, index, value):
        raise TypeError(f"ReadOnlyList は凍結されており変更できません (インデックス: {index})")

    def __delitem__(self, index):
        raise TypeError(f"ReadOnlyList は凍結されており変更できません (インデックス: {index})")

    def append(self, object):
        raise TypeError("ReadOnlyList は凍結されており変更できません")

    def extend(self, iterable):
        raise TypeError("ReadOnlyList は凍結されており変更できません")

    def insert(self, index, object):
        raise TypeError("ReadOnlyList は凍結されており変更できません")

    def pop(self, *args, **kwargs):
        raise TypeError("ReadOnlyList は凍結されており変更できません")

    def remove(self, value):
        raise TypeError("ReadOnlyList は凍結されており変更できません")

    def clear(self):
        raise TypeError("ReadOnlyList は凍結されており変更できません")

    def sort(self, *args, **kwargs):
        raise TypeError("ReadOnlyList は凍結されており変更できません")

    def reverse(self):
        raise TypeError("ReadOnlyList は変更できません")


def _deep_freeze_value(val: Any) -> Any:
    if isinstance(val, dict):
        return ReadOnlyDict({k: _deep_freeze_value(v) for k, v in val.items()})
    elif isinstance(val, list):
        return ReadOnlyList([_deep_freeze_value(x) for x in val])
    return val


def _deep_unfreeze_value(val: Any) -> Any:
    if isinstance(val, (dict, ReadOnlyDict)):
        return {k: _deep_unfreeze_value(v) for k, v in val.items()}
    elif isinstance(val, (list, ReadOnlyList)):
        return [_deep_unfreeze_value(x) for x in val]
    return val


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
    is_frozen: bool = False
    source_id: Optional[str] = None       # 固有の発行元・作成元ID (BASE v2.0 §4.2: ソース独立性)
    source_lineage: Optional[str] = None  # 上流系譜 (例: "manual_hr_v1", "policy_sec_2026")
    node_relations: Dict[str, str] = field(default_factory=dict) # 他ノードとの明示的関係: {node_id: "support" | "contradict" | "independent" | "unknown"}
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    last_updated: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def __setattr__(self, name: str, value: Any):
        if getattr(self, "is_frozen", False) and name != "is_frozen":
            raise RuntimeError(f"MBNode(id={getattr(self, 'id', '')}) は凍結(frozen)されています。属性 '{name}' の変更は禁止されています。")
        super().__setattr__(name, value)

    def __delattr__(self, name: str):
        if getattr(self, "is_frozen", False):
            raise RuntimeError(f"MBNode(id={getattr(self, 'id', '')}) は凍結(frozen)されています。属性 '{name}' の削除は禁止されています。")
        super().__delattr__(name)

    def freeze(self):
        """ノードを凍結（Deep Freeze: 属性代入および内部辞書・リストの変更を封殺）"""
        super().__setattr__("trigger_pattern", _deep_freeze_value(self.trigger_pattern))
        super().__setattr__("action_template", _deep_freeze_value(self.action_template))
        super().__setattr__("node_relations", _deep_freeze_value(self.node_relations))
        super().__setattr__("is_frozen", True)

    def unfreeze(self):
        """凍結解除"""
        super().__setattr__("is_frozen", False)
        super().__setattr__("trigger_pattern", _deep_unfreeze_value(self.trigger_pattern))
        super().__setattr__("action_template", _deep_unfreeze_value(self.action_template))

    def inertia(self) -> float:
        """
        関係拘束強度の「時間・更新抵抗断面」 I(M_B) （BASE v2.0 §4.2 / SPEC v2.0 §6.2）

        【重要な区別】
          - I(M_B) は「この構造が更新にどれだけ抵抗するか」の断面であり、
            関係拘束強度 C_rel そのものではない。
          - 大きい I は「変わりにくい」を意味し、「現在の問いへの正しさ」や
            「信頼性」を直接表すものではない。
          - confidence boost には使わない（constraint.py の constraint_score を使う）。

        使用箇所:
          - h_state.dissipate(): H の散逸率（I が高いほど冷えにくい）
          - content_hash(): 力学状態のハッシュ化（慣性を含む同一性保証）

        I(M_B) = max(0, confidence * (1 + 0.3*success + 0.5*approval - 0.5*failure - 0.8*rejection))
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
        if self.is_frozen:
            raise RuntimeError(f"MBNode(id={self.id}) は凍結(frozen)されています。学習・統計更新は禁止されています。")
        self.success_count += 1
        if approved:
            self.approval_count += 1
        self.confidence = min(1.0, self.confidence + 0.05)
        self.last_updated = datetime.utcnow().isoformat()

    def record_failure(self, rejected: bool = False):
        if self.is_frozen:
            raise RuntimeError(f"MBNode(id={self.id}) は凍結(frozen)されています。学習・統計更新は禁止されています。")
        self.failure_count += 1
        if rejected:
            self.rejection_count += 1
        self.confidence = max(0.1, self.confidence - 0.1)
        self.last_updated = datetime.utcnow().isoformat()


class MBGraph:
    def __init__(self, m0: float = 3.0, version: str = "v1.0", is_frozen: bool = False):
        self.nodes: Dict[str, MBNode] = {}
        self._key_index: Dict[str, set] = {}
        self._domain_index: Dict[str, set] = {}
        self.m0 = m0
        self.version = version
        self.is_frozen = is_frozen

    def _index_node(self, node: MBNode):
        d = node.domain or "general"
        if d not in self._domain_index:
            self._domain_index[d] = set()
        self._domain_index[d].add(node.id)

        for k in node.trigger_pattern.get("exact_keys", []):
            nk = k.strip().lower()
            if nk:
                if nk not in self._key_index:
                    self._key_index[nk] = set()
                self._key_index[nk].add(node.id)

    def _unindex_node(self, node: MBNode):
        d = node.domain or "general"
        if d in self._domain_index and node.id in self._domain_index[d]:
            self._domain_index[d].discard(node.id)
            if not self._domain_index[d]:
                del self._domain_index[d]

        for k in node.trigger_pattern.get("exact_keys", []):
            nk = k.strip().lower()
            if nk in self._key_index and node.id in self._key_index[nk]:
                self._key_index[nk].discard(node.id)
                if not self._key_index[nk]:
                    del self._key_index[nk]

    def freeze(self):
        """候補グラフおよび所属全ノードを完全固定（Deep Freeze: Canary中のIdentity Drift防止）"""
        self.is_frozen = True
        for node in self.nodes.values():
            node.freeze()

    def unfreeze(self):
        """凍結解除"""
        self.is_frozen = False
        for node in self.nodes.values():
            node.unfreeze()

    def content_hash(self) -> str:
        """
        グラフの論理的・力学的実体に対する暗号論的ハッシュ (SHA-256)
        ノード構造、ルール、アクション定義に加え、慣性質量 ||M_B|| と κ に直結する
        成功・失敗・承認・差し戻し回数、および m0 を完全包含する。
        （タイムスタンプ created_at / last_updated のみ除外）
        """
        canonical_nodes = []
        for nid in sorted(self.nodes.keys()):
            node = self.nodes[nid]
            n_dict = {
                "id": node.id,
                "domain": node.domain,
                "trigger_pattern": node.trigger_pattern,
                "action_template": node.action_template,
                "authority_level": node.authority_level,
                "confidence": round(node.confidence, 4),
                "success_count": node.success_count,
                "failure_count": node.failure_count,
                "approval_count": node.approval_count,
                "rejection_count": node.rejection_count,
            }
            if getattr(node, "source_id", None) is not None:
                n_dict["source_id"] = node.source_id
            if getattr(node, "source_lineage", None) is not None:
                n_dict["source_lineage"] = node.source_lineage
            if getattr(node, "node_relations", None):
                n_dict["node_relations"] = dict(sorted(node.node_relations.items()))
            canonical_nodes.append(n_dict)
        payload = {
            "version": self.version,
            "m0": round(self.m0, 4),
            "nodes": canonical_nodes,
        }
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def add_or_update(self, node: MBNode):
        if self.is_frozen:
            raise RuntimeError(f"MBGraph (version={self.version}) は凍結(frozen)されています。ノード {node.id} の変更・追加は禁止されています。")
        if node.id in self.nodes:
            self._unindex_node(self.nodes[node.id])
        self.nodes[node.id] = node
        self._index_node(node)

    def get(self, node_id: str) -> Optional[MBNode]:
        return self.nodes.get(node_id)

    def remove(self, node_id: str) -> bool:
        if self.is_frozen:
            raise RuntimeError(f"MBGraph (version={self.version}) は凍結(frozen)されています。ノード {node_id} の削除は禁止されています。")
        if node_id in self.nodes:
            self._unindex_node(self.nodes[node_id])
            del self.nodes[node_id]
            return True
        return False

    def list_nodes(self, domain: Optional[str] = None) -> List[MBNode]:
        if domain:
            if hasattr(self, "_domain_index") and domain in self._domain_index:
                return [self.nodes[nid] for nid in self._domain_index[domain] if nid in self.nodes]
            return [n for n in self.nodes.values() if n.domain == domain]
        return list(self.nodes.values())

    def find_co_occurring_nodes(self, node: MBNode, limit: int = 5) -> List[MBNode]:
        """
        指定ノードとトリガーキーを共有する同一ドメインのノード群を高速逆引き (O(keys))。
        推論ホットパスでの全ノード走査 O(N) を排除する。

        【決定的ランキング (Top-k Determinism: SPEC 4 / BASE v2.0 §4.2)】:
        候補が limit を超える場合でも、反復順序のブレを排除して完全に決定的な順序で選出する。
        ソート順:
          1. 共有トリガーキー数（降順）
          2. 承認数 approval_count（降順）
          3. confidence（降順）
          4. ノード ID（昇順: タイブレークにより完全な決定性を保証）
        """
        candidate_ids = set()
        my_keys = set(k.strip().lower() for k in node.trigger_pattern.get("exact_keys", []))
        if hasattr(self, "_key_index"):
            for nk in my_keys:
                if nk in self._key_index:
                    candidate_ids.update(self._key_index[nk])
        else:
            # インデックスがない場合のフォールバック
            for other in self.nodes.values():
                if other.id != node.id and other.domain == node.domain:
                    other_keys = set(k.strip().lower() for k in other.trigger_pattern.get("exact_keys", []))
                    if other_keys & my_keys:
                        candidate_ids.add(other.id)

        candidate_ids.discard(node.id)

        d = node.domain or "general"
        scored_candidates = []
        for cid in candidate_ids:
            cand = self.nodes.get(cid)
            if cand and cand.domain == d:
                cand_keys = set(k.strip().lower() for k in cand.trigger_pattern.get("exact_keys", []))
                overlap_count = len(my_keys & cand_keys)
                scored_candidates.append((
                    -overlap_count,
                    -cand.approval_count,
                    -cand.confidence,
                    cand.id,
                    cand,
                ))

        # 決定的ソート
        scored_candidates.sort()
        return [item[4] for item in scored_candidates[:limit]]

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
            is_frozen=False,  # まず解凍状態で初期化
        )
        for nid, ndict in data.get("nodes", {}).items():
            node_dict = dict(ndict)
            node_frozen = node_dict.pop("is_frozen", False)
            node = MBNode(**node_dict)
            if node_frozen:
                node.freeze()
            graph.add_or_update(node)
        if data.get("is_frozen", False):
            graph.freeze()
        return graph

    def save_json(self, filepath: str):
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load_json(cls, filepath: str) -> "MBGraph":
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
