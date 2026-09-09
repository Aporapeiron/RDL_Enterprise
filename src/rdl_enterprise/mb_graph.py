import hashlib
import json
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, List, Any
from rdl_core import CommitmentOrigin, CommitmentRecord, EvidencePolarity

class IntegrityError(Exception):
    """
    データ整合性・改ざん検知例外 (B5 Zero Trust: 完全性検証失敗)
    保存済み content_hash と復元後実効ハッシュの不一致、またはスナップショットハッシュの不整合時に送出。
    ※ Checksum (完全性・改ざん検出) であり、電子署名等の Authenticity (真正性) とは区別される。
    """
    pass


@dataclass(frozen=True)
class LegacySnapshot:
    """
    レガシーデータスナップショット (BASE v2.0 §4.2 / B5 Zero Trust: 移行データの真正性検証)
    移行対象のデータペイロードと期待されるハッシュ値を保持し、移行対象ノード群の同一性を拘束。
    """
    source_version: str
    raw_payload: Dict[str, Any]
    expected_source_hash: Optional[str] = None

    def compute_hash(self) -> str:
        serialized = json.dumps(self.raw_payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def get_target_node_ids(self) -> List[str]:
        """スナップショットが対象とするノードIDの集合を抽出"""
        if "nodes" in self.raw_payload and isinstance(self.raw_payload["nodes"], dict):
            return sorted(self.raw_payload["nodes"].keys())
        elif "uncommitted_nodes" in self.raw_payload and isinstance(self.raw_payload["uncommitted_nodes"], list):
            return sorted(n["id"] for n in self.raw_payload["uncommitted_nodes"] if isinstance(n, dict) and "id" in n)
        return []

    def get_node_payload(self, node_id: str) -> Optional[Dict[str, Any]]:
        """指定ノードIDのペイロードを抽出"""
        if "nodes" in self.raw_payload and isinstance(self.raw_payload["nodes"], dict):
            return self.raw_payload["nodes"].get(node_id)
        elif "uncommitted_nodes" in self.raw_payload and isinstance(self.raw_payload["uncommitted_nodes"], list):
            for n in self.raw_payload["uncommitted_nodes"]:
                if isinstance(n, dict) and n.get("id") == node_id:
                    return n
        return None


@dataclass(frozen=True)
class MigrationContext:
    """
    移行実行権威コンテキスト (BASE v2.0 §4.2: 移行検証責任者と権威・能力の明示)
    """
    verifier_id: str
    role: str  # "admin", "manager", "migration_officer"
    capability: str = "legacy_migration"
    timestamp: Optional[str] = None


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
        committed_at: Optional[str] = None,
        commitment_record: Optional[Dict[str, Any]] = None,
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

        # 【Commitment Authenticity (BASE v2.0 §4.2: Description != Commitment)】
        # 公開コンストラクタはいかなる引数を用いてもコミットメント状態を自己生成・偽装できません。
        # 外部引数 (commitment_origin, committed_at, commitment_record) は安全のため無視・無効化されます。
        # _internal_commitment 等のバイパス引数も完全撤去。
        # コミットメントの確立は MBGraph.commit_node() または厳格検証済みデシリアライザのみが
        # object.__setattr__ を介して行います。
        object.__setattr__(self, "_commitment_record", None)

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

        if is_frozen:
            self.is_frozen = True

    @property
    def commitment_origin(self) -> Optional[str]:
        """コミットメントの出所（読み取り専用）"""
        return self._commitment_record.origin if self._commitment_record else None

    @property
    def committed_at(self) -> Optional[str]:
        """コミット時刻（読み取り専用）"""
        return self._commitment_record.committed_at if self._commitment_record else None

    @property
    def commitment_record(self) -> Optional[Dict[str, Any]]:
        """構造化コミットメント証跡レコード（読み取り専用辞書表現）"""
        return self._commitment_record.to_dict() if self._commitment_record else None

    @property
    def is_committed(self) -> bool:
        """
        ノードが M_B に正式コミットされているかを判定。
        (CommitmentRecord が保持され、origin および committed_at が必須)
        """
        return (
            self._commitment_record is not None
            and self._commitment_record.origin is not None
            and self._commitment_record.committed_at is not None
        )

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
        if name in ("commitment_origin", "committed_at", "commitment_record", "_commitment_record"):
            raise AttributeError(f"属性 '{name}' は変更できません。コミットメントの変更・確立は MBGraph.commit_node() を経由してください。")
        super().__setattr__(name, value)

    def __delattr__(self, name: str):
        if getattr(self, "is_frozen", False):
            raise RuntimeError(f"MBNode(id={getattr(self, 'id', '')}) は凍結(frozen)されています。属性 '{name}' の削除は禁止されています。")
        super().__delattr__(name)

    def to_dict(self) -> Dict[str, Any]:
        """ノードの完全な辞書表現（シリアライズ・永続化用）"""
        d = asdict(self)
        if self.commitment_origin is not None:
            d["commitment_origin"] = self.commitment_origin
        if self.committed_at is not None:
            d["committed_at"] = self.committed_at
        if self.commitment_record is not None:
            d["commitment_record"] = self.commitment_record
        return d

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
          - content_hash(): 力学状態のハッシュ化（慣性を含む境界内同値性の検証）

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

    def record_success(self, approved: bool = False, at: Optional[Any] = None):
        if self.is_frozen:
            raise RuntimeError(f"MBNode(id={self.id}) は凍結(frozen)されています。学習・統計更新は禁止されています。")
        self.success_count += 1
        if approved:
            self.approval_count += 1
        self.confidence = min(1.0, self.confidence + 0.05)
        if at is not None:
            self.last_support_at = at.isoformat() if isinstance(at, datetime) else str(at)
        else:
            self.last_support_at = datetime.utcnow().isoformat()

    def record_failure(self, rejected: bool = False, at: Optional[Any] = None):
        if self.is_frozen:
            raise RuntimeError(f"MBNode(id={self.id}) は凍結(frozen)されています。学習・統計更新は禁止されています。")
        self.failure_count += 1
        if rejected:
            self.rejection_count += 1
        self.confidence = max(0.1, self.confidence - 0.1)
        if at is not None:
            self.last_opposing_at = at.isoformat() if isinstance(at, datetime) else str(at)
        else:
            self.last_opposing_at = datetime.utcnow().isoformat()

    def record_unresolved(self, at: Optional[Any] = None):
        """
        観測不能・タイムアウト（UNKNOWN）の記録。
        判断が誤っていたわけではないため、failure_count や confidence は減衰させず、
        未回収関係（ξ）の滞留・未解決観測として独立にカウントする。
        意味的証拠の更新（last_support_at / last_opposing_at）は行わず、観測タイムスタンプ（last_observed_at）のみを更新する。
        """
        if self.is_frozen:
            raise RuntimeError(f"MBNode(id={self.id}) は凍結(frozen)されています。学習・統計更新は禁止されています。")
        self.unresolved_count += 1
        if at is not None:
            self.last_observed_at = at.isoformat() if isinstance(at, datetime) else str(at)
        else:
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
        """候補グラフおよび所属全ノードを境界内固定（Deep Freeze: Canary中のIdentity Drift防止）"""
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
            if getattr(node, "committed_at", None) is not None:
                n_dict["committed_at"] = node.committed_at
            if getattr(node, "commitment_record", None) is not None:
                n_dict["commitment_record"] = dict(sorted(node.commitment_record.items()))
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
        origin: CommitmentOrigin,
        actor: Optional[str] = None,
        authority_context: Optional[Any] = None,
        evidence_time: Optional[datetime] = None,
        commit_time: Optional[datetime] = None,
    ) -> MBNode:
        """
        関係記述（Description）を正統な拘束として M_B にコミットする唯一の正規ゲートウェイ。
        (BASE v2.0 §4.2: Description != Commitment != Active Constraint)

        - 単なる MBNode(...) 記述オブジェクトは支持証拠を持たない (last_support_at is None, freshness=0.0)。
        - commit_node() を通過することで出所 (CommitmentOrigin) とコミット時刻 (committed_at)、
          および支持証拠時刻 (last_support_at) が付与され、初めて M_B への格納と推論への参画が認められる。
        - origin は CommitmentOrigin Enum の明示指定を義務付け（デフォルト引数の全廃）。未知値は ValueError。
        - origin='authority' の場合は AuthorityContext の存在・callable検証・ドメイン認可 (== True) が必須 (Fail-Closed)。
        - 支持証拠観測時刻 (evidence_time) と M_B コミット時刻 (commit_time) を明確に分離。
        """
        if self.is_frozen:
            raise RuntimeError(f"MBGraph (version={self.version}) は凍結(frozen)されています。ノード {node.id} のコミットは禁止されています。")

        # 0. 既存コミットメントの再コミット・出所上書きの禁止 (BASE v2.0 §4.2: 単一コミットモデル)
        if node.is_committed:
            raise ValueError(
                f"ノード '{node.id}' は既にコミットされています (origin='{node.commitment_origin}', "
                f"committed_at='{node.committed_at}')。コミットメント証跡の再コミット・上書きは禁止されています。"
            )

        # 1. CommitmentOrigin の厳格正規化と検証
        if not isinstance(origin, CommitmentOrigin):
            try:
                origin = CommitmentOrigin(origin)
            except (ValueError, TypeError):
                raise ValueError(
                    f"無効な CommitmentOrigin: {origin}。CommitmentOrigin Enum のみを許可します。"
                )

        if commit_time is not None:
            now_commit_iso = commit_time.isoformat() if isinstance(commit_time, datetime) else str(commit_time)
        elif authority_context is not None and getattr(authority_context, "timestamp", None):
            auth_ts = getattr(authority_context, "timestamp")
            now_commit_iso = auth_ts.isoformat() if isinstance(auth_ts, datetime) else str(auth_ts)
        else:
            now_commit_iso = datetime.utcnow().isoformat()

        origin_str = origin.value

        # 2. 出所別の正統性・来歴の確立
        if origin == CommitmentOrigin.AUTHORITY:
            if authority_context is None:
                raise PermissionError("権威コミット (origin='authority') には AuthorityContext が必須です")
            is_auth_func = getattr(authority_context, "is_authorized_for", None)
            if not callable(is_auth_func):
                raise PermissionError("AuthorityContext は callable な 'is_authorized_for(domain)' を実装している必要があります")
            if is_auth_func(node.domain) is not True:
                raise PermissionError(
                    f"Actor '{getattr(authority_context, 'actor_id', 'unknown')}' はドメイン '{node.domain}' に対する認可を持っていません"
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

        # 3. 支持証拠時刻とコミット時刻の分離
        # evidence_time が指定されていればそれを採用
        # 以下の場合は支持証拠を捏造しない (Fail-Closed):
        # - 反証のみノード (last_opposing_at があり last_support_at が None)
        # - レガシー移行 (origin == MIGRATION_VERIFIED かつ last_support_at が None)
        # - 曖昧なレガシー証拠 (legacy_evidence_at があり last_support_at が None)
        if evidence_time is not None:
            node.last_support_at = evidence_time.isoformat() if isinstance(evidence_time, datetime) else str(evidence_time)
        elif (
            node.last_support_at is None
            and node.last_opposing_at is None
            and node.legacy_evidence_at is None
            and origin != CommitmentOrigin.MIGRATION_VERIFIED
        ):
            node.last_support_at = now_commit_iso

        # 4. 不変コミットメント証跡レコードの確立 (P1: CommitmentRecord)
        rec = CommitmentRecord(
            origin=origin_str,
            committed_at=now_commit_iso,
            actor=actor or getattr(authority_context, "actor_id", None) or "system",
            evidence_at=node.last_support_at,
            lineage=node.source_lineage,
        )
        object.__setattr__(node, "_commitment_record", rec)

        self.add_or_update(node)
        return node

    def add_or_update(self, node: MBNode):
        if self.is_frozen:
            raise RuntimeError(f"MBGraph (version={self.version}) は凍結(frozen)されています。ノード {node.id} の変更・追加は禁止されています。")
        if not node.is_committed:
            raise ValueError(
                f"未コミットの記述ノード (node_id={node.id}, commitment_origin={node.commitment_origin}, "
                f"committed_at={node.committed_at}) を M_B に直接格納することは禁止されています。"
                f"必ず MBGraph.commit_node(node, origin, ...) を経由してください。"
            )
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
          4. ノード ID（昇順: タイブレークにより条件固定順序を維持）
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
            "nodes": {nid: node.to_dict() for nid, node in self.nodes.items()}
        }

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        verify_hash: bool = True,
        allow_uncommitted: bool = False,
    ) -> "MBGraph":
        """
        辞書データから MBGraph を復元。
        (BASE v2.0 §4.2 / B5 Zero Trust: 暗黙的コミット昇格の全廃・直列化データ真正性検証・完全性照合)

        - commitment_record の存在時は CommitmentRecord.from_dict_strict() で厳格検証。
        - allow_uncommitted=False (デフォルト) の場合、未コミットのノードが存在すれば
          add_or_update() で ValueError を送出して安全に遮断。
        - verify_hash=True (デフォルト) の場合、保存された content_hash と復元後グラフの
          content_hash() を厳格照合し、改ざん検出時は IntegrityError を送出。
        """
        graph = cls(
            m0=data.get("m0", 3.0),
            version=data.get("version", "v1.0"),
            is_frozen=False,  # まず解凍状態で初期化
        )
        for nid, ndict in data.get("nodes", {}).items():
            node_dict = dict(ndict)
            node_frozen = node_dict.pop("is_frozen", False)

            # 構造化コミットメント証跡の厳格復元 (P0: serialized-data forgery 排除)
            rec = None
            if "commitment_record" in node_dict and node_dict["commitment_record"] is not None:
                rec = CommitmentRecord.from_dict_strict(
                    node_dict.pop("commitment_record"),
                    outer_origin=node_dict.get("commitment_origin"),
                    outer_committed_at=node_dict.get("committed_at"),
                )
            elif node_dict.get("commitment_origin") or node_dict.get("committed_at"):
                # 外側フィールドのみで commitment_record が欠落しているものは完全性違反として拒絶
                raise ValueError(
                    f"ノード '{nid}' は commitment_record を欠いており、出所証跡が不完全です。"
                    f"外部属性のみによる自己申告コミットメントは拒絶されます。"
                )

            node = MBNode(**node_dict)
            if rec is not None:
                object.__setattr__(node, "_commitment_record", rec)

            if node_frozen:
                node.freeze()

            if allow_uncommitted and not node.is_committed:
                graph.nodes[node.id] = node
                graph._index_node(node)
            else:
                graph.add_or_update(node)

        # ハッシュ完全性検証 (P1: Load-time Content Hash Integrity Verification)
        if verify_hash and "content_hash" in data and data["content_hash"] is not None:
            expected_hash = data["content_hash"]
            actual_hash = graph.content_hash()
            if actual_hash != expected_hash:
                raise IntegrityError(
                    f"M_B 整合性検証失敗 (Content Hash Mismatch): "
                    f"保存されたハッシュ '{expected_hash}' に対し、復元データの実効ハッシュは '{actual_hash}' です。"
                    f"データが保存後に改ざんまたは不整合を起こしています。"
                )

        if data.get("is_frozen", False):
            graph.freeze()
        return graph

    def migrate_legacy_nodes(
        self,
        snapshot: LegacySnapshot,
        context: MigrationContext,
    ) -> int:
        """
        レガシー・未コミットノードを実検証を経て正統コミットメントへ昇格する明示的ゲートウェイ (P2)。
        (BASE v2.0 §4.2 / B5 Zero Trust: 実データハッシュ照合・検証責任者権威チェック・対象同一性拘束)

        - 厳格な型安全検査: LegacySnapshot および MigrationContext 以外の引数は TypeError で拒絶 (互換ラッパー全廃)。
        - 権威検証: context.role が admin, manager, migration_officer であること、capability == 'legacy_migration' であること。
        - 完全性照合: snapshot.compute_hash() と snapshot.expected_source_hash の境界内同値。
        - 対象バインディング: スナップショット対象ノード集合とグラフ未コミットノード集合の境界内同値、およびノード定義（domain, trigger_pattern, action_template）の一致を検証。
        """
        if self.is_frozen:
            raise RuntimeError(f"MBGraph (version={self.version}) は凍結(frozen)されています。移行は禁止されています。")

        if not isinstance(snapshot, LegacySnapshot) or not isinstance(context, MigrationContext):
            raise TypeError(
                "migrate_legacy_nodes には (snapshot: LegacySnapshot, context: MigrationContext) を指定してください。"
                "互換引数は廃止されました。"
            )

        # 1. 移行権威・ロールの検証 (admin, manager, migration_officer のみを許可)
        allowed_roles = {"admin", "manager", "migration_officer"}
        if context.role not in allowed_roles:
            raise PermissionError(
                f"移行権威不足: Role '{context.role}' (actor: '{context.verifier_id}') は "
                f"レガシー移行を実行する権限がありません。許可ロール: {allowed_roles}"
            )
        if context.capability != "legacy_migration":
            raise PermissionError(f"移行ケイパビリティ不足: '{context.capability}'")

        # 2. 実データハッシュの照合 (Integrity Check)
        computed_hash = snapshot.compute_hash()
        if snapshot.expected_source_hash is not None:
            if computed_hash != snapshot.expected_source_hash:
                raise IntegrityError(
                    f"レガシースナップショットのハッシュ不一致: "
                    f"期待値 '{snapshot.expected_source_hash}' vs 実効値 '{computed_hash}'"
                )

        # 3. スナップショット ↔ 移行対象ノード群の完全同一性バインディング (Identity Binding)
        target_ids = set(snapshot.get_target_node_ids())
        if not target_ids:
            raise ValueError("スナップショットに対象ノードが含まれていません")

        graph_uncommitted_ids = {n.id for n in self.nodes.values() if not n.is_committed}
        if graph_uncommitted_ids != target_ids:
            raise IntegrityError(
                f"スナップショット対象とグラフ未コミットノード集合の不一致（すり替え・過不足検知）: "
                f"スナップショット対象={sorted(target_ids)} vs グラフ未コミット={sorted(graph_uncommitted_ids)}"
            )

        # 各ノードの内容（ドメイン・トリガー・アクション）の境界内同値検証
        for nid in target_ids:
            node = self.nodes[nid]
            snap_payload = snapshot.get_node_payload(nid)
            if not snap_payload:
                raise IntegrityError(f"スナップショット内にノード '{nid}' のペイロードが見つかりません")
            if node.domain != snap_payload.get("domain"):
                raise IntegrityError(f"ノード '{nid}' の domain がスナップショットと不一致です")
            if node.trigger_pattern != snap_payload.get("trigger_pattern"):
                raise IntegrityError(f"ノード '{nid}' の trigger_pattern がスナップショットと不一致です")
            if node.action_template != snap_payload.get("action_template"):
                raise IntegrityError(f"ノード '{nid}' の action_template がスナップショットと不一致です")

        # 4. 検証済みノードの正統コミットメント確立
        migrated_count = 0
        now_iso = (context.timestamp or datetime.utcnow().isoformat())
        lineage = f"migration:{snapshot.source_version}:{context.verifier_id}:{computed_hash[:12]}"

        for nid in sorted(target_ids):
            node = self.nodes[nid]
            was_frozen = node.is_frozen
            if was_frozen:
                node.unfreeze()

            rec = CommitmentRecord(
                origin=CommitmentOrigin.MIGRATION_VERIFIED.value,
                committed_at=now_iso,
                actor=context.verifier_id,
                evidence_at=node.last_support_at,
                lineage=lineage,
            )
            object.__setattr__(node, "_commitment_record", rec)
            if was_frozen:
                node.freeze()
            migrated_count += 1

        return migrated_count

    def save_json(self, filepath: str):
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load_json(cls, filepath: str) -> "MBGraph":
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
