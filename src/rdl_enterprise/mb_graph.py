import hashlib
import json
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, List, Any


class EvidencePolarity(str, Enum):
    """確定証拠の極性 (BASE v2.0 §4.2: evidence != supporting evidence)"""
    SUPPORT = "support"
    OPPOSE = "oppose"
    UNRESOLVED = "unresolved"


class CommitmentOrigin(str, Enum):
    """M_B へのコミットメント出所・正統性 (BASE v2.0 §4.2: Description != Commitment)"""
    AUTHORITY = "authority"                # 認可された方針注入 (inject_authoritative_rule)
    VERIFIED_EXPERIENCE = "experience"    # 解決確認済みの経験沈澱 (crystallize_rule)
    AUTHORITATIVE_SEED = "seed"           # 明示的シード知識 (seed load)
    PROMOTION = "promotion"               # M_Δ ハーネス・シャドウ通過昇格 (Leap)
    MIGRATION_VERIFIED = "migration"      # 来歴検証済み移行
    TEST_FIXTURE = "test_fixture"         # テスト用明示コミット

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
    unresolved_count: int = 0
    is_frozen: bool = False
    source_id: Optional[str] = None       # 固有の発行元・作成元ID (BASE v2.0 §4.2: ソース独立性)
    source_lineage: Optional[str] = None  # 上流系譜 (例: "manual_hr_v1", "policy_sec_2026")
    node_relations: Dict[str, str] = field(default_factory=dict) # 他ノードとの明示的関係: {node_id: "support" | "contradict" | "independent" | "unknown"}
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    last_support_at: Optional[str] = None
    last_opposing_at: Optional[str] = None
    last_observed_at: Optional[str] = None
    legacy_evidence_at: Optional[str] = None
    commitment_origin: Optional[str] = None

    def __init__(
        self,
        id: str,
        domain: str,
        trigger_pattern: Dict[str, Any],
        action_template: Dict[str, Any],
        authority_level: str = "auto",
        confidence: float = 0.5,
        success_count: int = 0,
        failure_count: int = 0,
        approval_count: int = 0,
        rejection_count: int = 0,
        unresolved_count: int = 0,
        is_frozen: bool = False,
        source_id: Optional[str] = None,
        source_lineage: Optional[str] = None,
        node_relations: Optional[Dict[str, str]] = None,
        created_at: Optional[str] = None,
        last_support_at: Optional[str] = None,
        last_opposing_at: Optional[str] = None,
        last_observed_at: Optional[str] = None,
        legacy_evidence_at: Optional[str] = None,
        last_evidence_at: Optional[str] = None,
        last_updated: Optional[str] = None,
        commitment_origin: Optional[str] = None,
    ):
        self.is_frozen = False
        self.id = id
        self.domain = domain
        self.trigger_pattern = trigger_pattern
        self.action_template = action_template
        self.authority_level = authority_level
        self.confidence = confidence
        self.success_count = success_count
        self.failure_count = failure_count
        self.approval_count = approval_count
        self.rejection_count = rejection_count
        self.unresolved_count = unresolved_count
        self.source_id = source_id
        self.source_lineage = source_lineage
        self.node_relations = node_relations if node_relations is not None else {}
        self.commitment_origin = commitment_origin

        def _to_iso(val: Any) -> Optional[str]:
            if val is None:
                return None
            if isinstance(val, datetime):
                return val.isoformat()
            return str(val)

        self.created_at = _to_iso(created_at) or datetime.utcnow().isoformat()
        self.last_support_at = _to_iso(last_support_at)
        self.last_opposing_at = _to_iso(last_opposing_at)
        self.last_observed_at = _to_iso(last_observed_at)
        self.legacy_evidence_at = _to_iso(legacy_evidence_at)

        # レガシー移行処理 (B5: 極性の無断捏造禁止・混在履歴の完全フェイルクローズ)
        # last_updated または last_evidence_at が渡され、かつ last_support_at が未指定の場合:
        legacy_val = _to_iso(last_evidence_at if last_evidence_at is not None else last_updated)
        if legacy_val is not None and self.last_support_at is None and self.last_opposing_at is None:
            has_support = bool(success_count > 0 or approval_count > 0 or authority_level == "policy")
            has_oppose = bool(failure_count > 0 or rejection_count > 0)
            if has_support and not has_oppose:
                # 明示的な成功・承認実績（または権威コミット）のみが存在する場合のみ SUPPORT 証拠として移行
                self.last_support_at = legacy_val
            elif has_oppose and not has_support:
                # 失敗・拒絶実績のみが存在する場合は OPPOSE 証拠として移行
                self.last_opposing_at = legacy_val
            else:
                # 両方混在（最後の更新極性が不明）、または実績ゼロ:
                # 極性を一切捏造せず legacy_evidence_at (ξ) としてのみ保持し、last_support_at / last_opposing_at は None のままとする (Fail-Closed)
                pass
            self.legacy_evidence_at = legacy_val

        # 【Description != Commitment (BASE v2.0 §4.2)】
        # ノードの単なるオブジェクト生成（記述）をもって正の支持証拠（last_support_at）を自己生成・捏造することを禁止。
        # 正式なコミットメント（commit_node / crystallize / inject / seed）を経るまで last_support_at は None のままとする。

        if is_frozen:
            self.is_frozen = True

    @property
    def last_evidence_at(self) -> Optional[str]:
        """
        後方互換・監査用プロパティ。
        最後に確定証拠（支持または反証）が到来した最新時刻。
        一度も確定証拠が観測されていない場合は None (ξ) を返す。
        ※Core freshness（支持鮮度）の計算には直接使ってはならない。
        """
        candidates = [x for x in (self.last_support_at, self.last_opposing_at) if x]
        return max(candidates) if candidates else None

    @last_evidence_at.setter
    def last_evidence_at(self, value: Any):
        raise AttributeError(
            "last_evidence_at は読み取り専用です。極性に応じた更新 "
            "(record_success, record_failure, record_unresolved, last_support_at, last_opposing_at) を使用してください。"
        )

    @property
    def last_updated(self) -> Optional[str]:
        """後方互換用プロパティ（last_evidence_at への委譲）"""
        return self.last_evidence_at

    @last_updated.setter
    def last_updated(self, value: Any):
        raise AttributeError(
            "last_updated は読み取り専用です。極性に応じた更新 "
            "(record_success, record_failure, record_unresolved, last_support_at, last_opposing_at) を使用してください。"
        )

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
        super().__setattr__("node_relations", _deep_unfreeze_value(self.node_relations))

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
        self.last_support_at = datetime.utcnow().isoformat()

    def record_failure(self, rejected: bool = False):
        if self.is_frozen:
            raise RuntimeError(f"MBNode(id={self.id}) は凍結(frozen)されています。学習・統計更新は禁止されています。")
        self.failure_count += 1
        if rejected:
            self.rejection_count += 1
        self.confidence = max(0.1, self.confidence - 0.1)
        self.last_opposing_at = datetime.utcnow().isoformat()

    def record_unresolved(self):
        """
        観測不能・タイムアウト（UNKNOWN）の記録。
        判断が誤っていたわけではないため、failure_count や confidence は減衰させず、
        未回収関係（ξ）の滞留・未解決観測として独立にカウントする。
        意味的証拠の更新（last_support_at / last_opposing_at）は行わず、観測タイムスタンプ（last_observed_at）のみを更新する。
        """
        if self.is_frozen:
            raise RuntimeError(f"MBNode(id={self.id}) は凍結(frozen)されています。学習・統計更新は禁止されています。")
        self.unresolved_count += 1
        self.last_observed_at = datetime.utcnow().isoformat()


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
        成功・失敗・承認・差し戻し回数、m0、および行動状態・時間拘束（freshness / opposing）に直結する
        last_support_at および last_opposing_at を完全包含する。
        （過渡的観測残差 ξ である created_at / last_observed_at / unresolved_count / legacy_evidence_at のみ除外）
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
                "last_support_at": node.last_support_at,
                "last_opposing_at": node.last_opposing_at,
            }
            if getattr(node, "commitment_origin", None) is not None:
                n_dict["commitment_origin"] = node.commitment_origin
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

    def commit_node(
        self,
        node: MBNode,
        origin: CommitmentOrigin = CommitmentOrigin.TEST_FIXTURE,
        actor: Optional[str] = None,
        authority_context: Optional[Any] = None,
        commit_time: Optional[datetime] = None,
    ) -> MBNode:
        """
        関係記述（Description）を正統な拘束として M_B にコミットする唯一の正規ゲートウェイ。
        (BASE v2.0 §4.2: Description != Commitment != Active Constraint)

        - 単なる MBNode(...) 記述オブジェクトは支持証拠を持たない (last_support_at is None, freshness=0.0)。
        - commit_node() を通過することで出所 (CommitmentOrigin) とコミット時刻 (last_support_at) が付与され、
          初めて活性化拘束サブグラフ選定・推論の正統な構成要素となる。
        - origin='authority' の場合は有効な AuthorityContext によるドメイン認可が必須 (Fail-Closed)。
        """
        if self.is_frozen:
            raise RuntimeError(f"MBGraph (version={self.version}) は凍結(frozen)されています。ノード {node.id} のコミットは禁止されています。")

        now_iso = (commit_time or datetime.utcnow()).isoformat()
        origin_str = origin.value if isinstance(origin, CommitmentOrigin) else str(origin)

        if origin == CommitmentOrigin.AUTHORITY:
            if authority_context is None:
                raise PermissionError("権威コミット (origin='authority') には AuthorityContext が必須です")
            if hasattr(authority_context, "is_authorized_for") and not authority_context.is_authorized_for(node.domain):
                raise PermissionError(
                    f"Actor '{getattr(authority_context, 'actor_id', '')}' with role '{getattr(authority_context, 'role', '')}' "
                    f"is not authorized for domain '{node.domain}'"
                )
            node.authority_level = "policy"
            role = getattr(authority_context, "role", "policy")
            actor_id = getattr(authority_context, "actor_id", actor or "system")
            node.source_id = actor_id
            node.source_lineage = f"authority:{role}:{actor_id}"

        elif origin == CommitmentOrigin.VERIFIED_EXPERIENCE:
            node.source_lineage = node.source_lineage or "sedimentation:experience"

        elif origin == CommitmentOrigin.AUTHORITATIVE_SEED:
            node.source_lineage = node.source_lineage or "seed:authoritative"

        elif origin == CommitmentOrigin.PROMOTION:
            promoter = actor or "shadow_leap"
            node.source_lineage = node.source_lineage or f"promotion:{promoter}"

        node.commitment_origin = origin_str

        # コミット時に初めて正の支持証拠打刻が行われる (Description -> Commitment)
        if node.last_support_at is None:
            node.last_support_at = now_iso

        self.add_or_update(node)
        return node

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
