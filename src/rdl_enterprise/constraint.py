"""
constraint.py ― Relational Constraint レイヤー
(BASE v2.0 §4 / SPEC v2.0 §6.2 準拠)

関係拘束強度 C_rel は「問い・時点・境界・関係位置によって相対的に立ち上がる」という
BASE v2.0 公理を実装層で体現する。

設計の核心：
    拘束 C ──┬→ confidence への寄与（InterpCascade で使用）
              ├→ E の重みづけ（衝突した拘束が強いほど H 蓄積が増加）
              ├→ inertia I(M_B)（更新抵抗断面 — mb_graph.py で従来通り管理）
              └→ crystallization / selection weight（将来）

単一ノードではなく「関係の束（ConstraintBundle）」で拘束位置を表現する。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
import re


# ---------------------------------------------------------------------------
# ConstraintConfig: 各拘束断面の重みを独立管理（CascadeConfig と分離）
# ---------------------------------------------------------------------------

@dataclass
class ConstraintConfig:
    """
    拘束評価の設定（各断面の重みおよびパラメータ）
    CascadeConfig（推論設定）と分離して独立性を確保。
    """
    # 拘束スコア計算の断面重み（合計 1.0 になるように正規化される）
    w_relevance: float = 0.35     # 現在の問いへの適合
    w_freshness: float = 0.25     # 時間的新鮮さ
    w_authority: float = 0.15     # 制度的権限
    w_source: float = 0.15        # ソース拘束（承認比率）
    w_convergence: float = 0.10   # 複数独立関係の収束一致

    # freshness 計算パラメータ
    freshness_half_life_days: float = 90.0   # この日数で freshness が 0.5 に低下

    # bridge 検出パラメータ
    bridge_coverage_drop_threshold: float = 0.4  # ノード除去時にこの割合以上 coverage が落ちたら bridge と判定

    # 破断検査（RuptureProbe）パラメータ
    rupture_freshness_threshold: float = 0.2     # freshness がこれ未満なら break 候補
    rupture_rejection_ratio_threshold: float = 0.4  # rejection_count / total がこれ以上なら break 候補

    # 生存判定 (Survive) のための摂動・実績閾値
    # (B4/B5: 検査していない・実績が希薄なものは survive と呼ばず unresolved とする)
    min_survive_approvals: int = 3       # 最低限必要な承認実績数
    min_survive_relevance: float = 0.4   # 最低限必要な適合度

    # Cascade への confidence boost 上限（cascade.py が参照）
    constraint_boost_cap: float = 0.15


# ---------------------------------------------------------------------------
# ConstraintContext: 評価時の環境情報
# ---------------------------------------------------------------------------

@dataclass
class ConstraintContext:
    """
    関係拘束評価時の有限境界 B の情報（問い・時刻・バージョン・ドメイン）
    """
    efp: object                           # BusinessInput
    current_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    mb_version: str = "prod"
    active_domain: Optional[str] = None
    config: ConstraintConfig = field(default_factory=ConstraintConfig)
    frozen_context: Optional[Any] = None  # FrozenInterpretationContext（完全同一解釈器を伝播）
    llm_bridge: Optional[Any] = None      # 外部推論器 (LLM Bridge: Counterfactual Replay用)
    actual_replay_token: Optional[Any] = None # 事前予測 F を実際に形成した外生固定条件 K_actual


# ---------------------------------------------------------------------------
# ConstraintBundle: 拘束束（単一ノードではなく関係の束）
# ---------------------------------------------------------------------------

@dataclass
class BundleCore:
    """現在 F を実際に拘束している確定関係構造 (M_B[bundle])"""
    node_ids: List[str]                  # primary + explicit support
    constraint_score: float              # 確定拘束強度 ∈ [0, 1]
    convergence: float = 0.0             # 確定関係群の収束一致度 ∈ [0, 1]
    relevance: float = 0.0
    freshness: float = 0.0
    authority_weight: float = 0.0
    source_strength: float = 0.0
    source_lineages: List[str] = field(default_factory=list)


@dataclass
class BundleAuxiliary:
    """まだ M_B の確定拘束として扱えない未回収関係 (ξ evidence)"""
    inferred_node_ids: List[str] = field(default_factory=list)
    constraint_signal: float = 0.0       # 潜在的拘束シグナル ∈ [0, 1]
    convergence_signal: float = 0.0      # 潜在的収束シグナル ∈ [0, 1]
    observation_count: int = 0           # 観測反復回数（P4 昇格代謝用）
    independent_sources: List[str] = field(default_factory=list)  # 観測された独立ソース群


class ConstraintBundle:
    """
    「関係の束」として表現した拘束位置。
    確定拘束（core: BundleCore）と未確定関係（auxiliary: BundleAuxiliary）のみを
    唯一の実体 (canonical source of truth) として保持する。

    BASE v2.0: 強い場所とは「そこを外す・反転する・揺らすと、
    現在の解釈可能域が大きく変わる場所」。
    """
    def __init__(
        self,
        node_ids: Optional[List[str]] = None,
        locus_type: str = "strong",
        constraint_score: float = 0.0,
        core: Optional[BundleCore] = None,
        auxiliary: Optional[BundleAuxiliary] = None,
        relevance: float = 0.0,
        freshness: float = 0.0,
        authority_weight: float = 0.0,
        source_strength: float = 0.0,
        convergence: float = 0.0,
        is_structural_bridge: bool = False,
        inferred_node_ids: Optional[List[str]] = None,
        auxiliary_constraint_signal: float = 0.0,
        auxiliary_convergence_signal: float = 0.0,
        observation_count: int = 0,
        independent_sources: Optional[List[str]] = None,
    ):
        if core is not None:
            self.core = core
        else:
            self.core = BundleCore(
                node_ids=list(node_ids or []),
                constraint_score=constraint_score,
                convergence=convergence,
                relevance=relevance,
                freshness=freshness,
                authority_weight=authority_weight,
                source_strength=source_strength,
            )
        if auxiliary is not None:
            self.auxiliary = auxiliary
        else:
            self.auxiliary = BundleAuxiliary(
                inferred_node_ids=list(inferred_node_ids or []),
                constraint_signal=auxiliary_constraint_signal,
                convergence_signal=auxiliary_convergence_signal,
                observation_count=observation_count,
                independent_sources=list(independent_sources or []),
            )
        self.locus_type = locus_type
        self.is_structural_bridge = is_structural_bridge

    # --- 確定拘束 (core) への完全委譲プロパティ ---
    @property
    def node_ids(self) -> List[str]:
        return self.core.node_ids

    @property
    def constraint_score(self) -> float:
        return self.core.constraint_score

    @property
    def core_constraint_score(self) -> float:
        return self.core.constraint_score

    @property
    def convergence(self) -> float:
        return self.core.convergence

    @property
    def core_convergence(self) -> float:
        return self.core.convergence

    @property
    def relevance(self) -> float:
        return self.core.relevance

    @property
    def freshness(self) -> float:
        return self.core.freshness

    @property
    def authority_weight(self) -> float:
        return self.core.authority_weight

    @property
    def source_strength(self) -> float:
        return self.core.source_strength

    # --- 未回収関係 (auxiliary ξ) への完全委譲プロパティ ---
    @property
    def inferred_node_ids(self) -> List[str]:
        return self.auxiliary.inferred_node_ids

    @property
    def auxiliary_constraint_signal(self) -> float:
        return self.auxiliary.constraint_signal

    @property
    def auxiliary_convergence_signal(self) -> float:
        return self.auxiliary.convergence_signal

    def primary_node_id(self) -> Optional[str]:
        """代表ノード ID（最初の要素）"""
        return self.core.node_ids[0] if self.core.node_ids else None

    @property
    def supporting_node_ids(self) -> List[str]:
        """束に含まれる確定支援ノード群（代表ノード以外）"""
        return self.core.node_ids[1:] if len(self.core.node_ids) > 1 else []


# ---------------------------------------------------------------------------
# RuptureResult: 破断検査の結果
# ---------------------------------------------------------------------------

@dataclass
class RuptureResult:
    """
    RuptureProbe による破断検査の結果。
    BASE v2.0: 強い = 正しい、ではなく「現在の構造を支えているか」を問う。

    【変化量と判定の直交分離】
    - rupture_effect: 束切断による F の変化量 Δ(F_base, F_without_bundle) ∈ [0, 1]（切ると変わる＝拘束が強い）
    - verdict: survive | break | unresolved（切ると対立解釈が出る＝競合している）
    """
    bundle: ConstraintBundle
    verdict: str              # "survive" | "break" | "unresolved"
    opposing_strength: float  # 反証拘束の強さ（add_heat の重みとして使用）
    rupture_reason: str = ""
    rupture_effect: Optional[float] = None  # 束切断摂動による F の変化量 Δ(F_base, F_without) ∈ [0, 1] (None = 未測定)
    # --- 反実仮想監査証跡 (BASE v2.0 §4.2) ---
    conditions_hash: Optional[str] = None       # 外生固定条件 K の意味的決定性ハッシュ
    base_mb_view_hash: Optional[str] = None     # 切断前 M_B の view_hash
    cut_mb_view_hash: Optional[str] = None      # 切断後 M_B \ bundle の view_hash
    intervention_verified: bool = False         # Level 3 で M_B 介入が実証されたか
    base_trace_id: Optional[str] = None         # f_base の InterpretationTrace trace_id
    cut_trace_id: Optional[str] = None          # f_without の InterpretationTrace trace_id
    effect_verified_bundle_ids: List[str] = field(default_factory=list)  # 束集合切断 (set-level) で有意差が実証されたノード集合
    effect_verified_node_ids: List[str] = field(default_factory=list)    # 個別アブレーション (node-level) で因果責任が実証されたノード群
    effect_verified_locus_ids: List[str] = field(default_factory=list)  # 後方互換用 (bundle_ids と同一)


# ---------------------------------------------------------------------------
# 内部ユーティリティ
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text.lower())


def _is_payload_contradictory(a: str, b: str) -> bool:
    """
    同一 action_type であっても、payload レベルで明白な対立極性・矛盾があるかを検出。
    BASE v2.0: 「申請可能です」と「申請は禁止です」のような矛盾関係を同一束に入れない。
    """
    negations = ["禁止", "不可", "却下", "無効", "不許可", "停止", "否認", "できません", "できない", "認められない"]
    affirmatives = ["可能", "許可", "承認", "有効", "申請できます", "できる", "認められる"]

    na, nb = a.lower(), b.lower()
    a_has_neg = any(w in na for w in negations)
    b_has_neg = any(w in nb for w in negations)
    a_has_aff = any(w in na for w in affirmatives)
    b_has_aff = any(w in nb for w in affirmatives)

    if (a_has_neg and b_has_aff) or (a_has_aff and b_has_neg):
        return True
    return False


def _check_node_relation(node: object, candidate: object) -> str:
    """
    node と candidate 間の関係性を評価。
    返り値: "support" | "contradict" | "independent" | "unknown" | "inferred_support"
    """
    n_rel = getattr(node, "node_relations", {}).get(candidate.id)
    c_rel = getattr(candidate, "node_relations", {}).get(node.id)
    if n_rel == "contradict" or c_rel == "contradict":
        return "contradict"
    if n_rel == "independent" or c_rel == "independent":
        return "independent"
    if n_rel == "unknown" or c_rel == "unknown":
        return "unknown"
    if n_rel == "support" or c_rel == "support":
        return "support"

    # 未指定の場合: action_type 不一致または payload 極性矛盾があれば contradict
    if node.action_template.get("type") != candidate.action_template.get("type"):
        return "contradict"

    p_node = str(node.action_template.get("payload", ""))
    p_cand = str(candidate.action_template.get("payload", ""))
    if _is_payload_contradictory(p_node, p_cand):
        return "contradict"

    # payload が同一であれば同一言明の補強として support
    if p_node == p_cand:
        return "support"

    # action_type は同一だが payload が異なる（未指定）場合は inferred_support（推論された支援）
    return "inferred_support"


def _is_deterministic_replay_capable(bridge: Optional[object]) -> bool:
    """
    LLM推論器が「同一推論作用の再生（deterministic replay）」を契約として保証できるかを検証。
    BASE v2.0: 「同じモデル名」ではなく「同じ推論作用」を保証する問題。
    単なる seed + temperature=0 のみでは外部provider揺らぎを排除できないため不可。
    明示的な can_replay() 契約、deterministic_replay、または固定 response snapshot hash を要求。
    """
    if bridge is None:
        return False

    # 契約 1: can_replay() メソッドを実装している場合
    if hasattr(bridge, "can_replay") and callable(bridge.can_replay):
        try:
            return bool(bridge.can_replay())
        except Exception:
            return False

    # 契約 2: deterministic_replay=True または is_deterministic=True
    if getattr(bridge, "deterministic_replay", False) or getattr(bridge, "is_deterministic", False):
        return True

    # 契約 3: 有効な replay_snapshot_hash が設定されている場合
    snap = str(getattr(bridge, "replay_snapshot_hash", getattr(bridge, "snapshot_hash", "none")))
    if snap and snap != "none":
        return True

    return False


def _bigram_jaccard(a: str, b: str) -> float:
    """文字 bi-gram Jaccard 類似度"""
    na, nb = _normalize(a), _normalize(b)
    if len(na) < 2 and len(nb) < 2:
        return 1.0 if na == nb else 0.0
    def bigrams(s: str):
        return set(s[i:i+2] for i in range(len(s) - 1)) if len(s) >= 2 else {s}
    bg_a, bg_b = bigrams(na), bigrams(nb)
    if not bg_a and not bg_b:
        return 0.0
    return len(bg_a & bg_b) / len(bg_a | bg_b)


def _compute_relevance(query: str, pattern: dict) -> float:
    """現在の問い query とノードのトリガー pattern の適合度 ∈ [0, 1]"""
    keys = pattern.get("exact_keys", [])
    norm_q = _normalize(query)
    best_rel = 0.0
    for k in keys:
        norm_k = _normalize(k)
        if norm_k and (norm_k in norm_q or norm_q in norm_k):
            best_rel = max(best_rel, 0.8)
        else:
            best_rel = max(best_rel, _bigram_jaccard(query, k))

    rule_expr = pattern.get("rule_expr")
    if rule_expr:
        try:
            if re.search(rule_expr, query, re.IGNORECASE):
                best_rel = max(best_rel, 0.8)
        except re.error:
            pass
    return best_rel


def _compute_freshness(last_updated_iso: str, half_life_days: float, now: datetime) -> float:
    """
    最終更新日からの経過日数をもとに freshness ∈ [0, 1] を算出。
    half_life_days 経過で 0.5、それ以降は指数減衰。
    """
    try:
        updated = datetime.fromisoformat(last_updated_iso.replace("Z", "+00:00"))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        elapsed_days = max(0.0, (now - updated).total_seconds() / 86400.0)
        # 指数減衰: freshness = 0.5^(elapsed / half_life)
        import math
        return math.exp(-math.log(2) * elapsed_days / max(1.0, half_life_days))
    except Exception:
        return 0.5  # パース失敗時はニュートラル値


def _compute_authority_weight(authority_level: str) -> float:
    """authority_level を数値に変換"""
    return {
        "human_only": 1.0,
        "require_approval": 0.8,
        "auto": 0.4,
    }.get(authority_level, 0.4)


def _compute_source_strength(approval_count: int, rejection_count: int) -> float:
    """ソース拘束：承認比率 = approval / (approval + rejection + 1)"""
    return approval_count / (approval_count + rejection_count + 1.0)


def _compute_constraint_score(
    relevance: float,
    freshness: float,
    authority_weight: float,
    source_strength: float,
    convergence: float,
    cfg: ConstraintConfig,
) -> float:
    """
    各断面を重みづけして総合拘束スコアを算出 ∈ [0, 1]。
    BASE v2.0 §4.2: 拘束強度は各断面において相対的に立ち上がる。
    """
    total_w = (cfg.w_relevance + cfg.w_freshness + cfg.w_authority
               + cfg.w_source + cfg.w_convergence)
    if total_w <= 0:
        return 0.0
    score = (
        cfg.w_relevance   * relevance
        + cfg.w_freshness   * freshness
        + cfg.w_authority   * authority_weight
        + cfg.w_source      * source_strength
        + cfg.w_convergence * convergence
    ) / total_w
    return min(1.0, max(0.0, score))


# ---------------------------------------------------------------------------
# 後続 EFP' 側の拘束抽出 (C_prime)
# ---------------------------------------------------------------------------

def compute_efp_prime_constraint(
    feedback: object,                  # FeedbackResult
    snapshot: Optional[object] = None, # CaseSnapshot
    current_time: Optional[datetime] = None,
) -> float:
    """
    後続作用 EFP' およびフィードバックから抽出される対向関係拘束強度 C_prime ∈ [0.1, 1.0]
    (BASE v2.0 §4.2: 外界から入ってきた後続情報が持つ関係拘束の強さ)

    【真正な Provenance 評価】:
      「誰が・どの関係位置から・何を・いつ・どの媒体/制度経路を通して報告したか」を評価する。
      - 制度的記録・公式決定 (is_authoritative=True): C_prime = 1.0
      - 管理者・監査権限 (source_type="admin" / "oracle", authority_level="human_only"): 0.95
      - 監査ログ・監査権限 (source_type="audit"): 0.90
      - 先輩・上級権限 (source_type="senior", authority_level="require_approval"): 0.85
      - 未特定・一般人間フィードバック (source_type="human_feedback"): 0.70
      - 一般ユーザー (source_type="user"): 0.40
      - 伝達経路 (official_doc, audit_log, admin_override): チャネル加算
      - 反証の実質性 (correction_content, new_knowledge_provided): 具現性加算
      - 時点拘束 (observed_at): 報告時刻の新鮮さによる時間的減衰

    【認識論的境界の明示】:
      - 内容（クエリ・是正指示・反作用状態）: M_B が解釈して不整合 E = Δ(F, F') を形成する。
      - 来歴（誰が・どの権限・経路・時点で報告したか）: 解釈内容ではなく、その反証が持つ関係拘束強度 C_prime を形成し、
        不整合 E を系内にどれだけ激しく保持するか（熱 H = E × opposing_strength）を決定する。
    """
    prov = getattr(feedback, "provenance", None)

    # 1. 来歴の制度的・権威的位置付け
    if prov is not None:
        if getattr(prov, "is_authoritative", False):
            auth_weight = 1.0
        elif getattr(prov, "source_type", "") in ("admin", "oracle") or getattr(prov, "authority_level", "") == "human_only":
            auth_weight = 0.95
        elif getattr(prov, "source_type", "") == "audit":
            auth_weight = 0.90
        elif getattr(prov, "source_type", "") == "senior" or getattr(prov, "authority_level", "") == "require_approval":
            auth_weight = 0.85
        elif getattr(prov, "source_type", "") == "human_feedback":
            auth_weight = 0.70
        else:
            auth_weight = 0.40

        # 伝達経路・チャネルの重み加算
        channel = getattr(prov, "channel", "standard")
        channel_boost = {
            "official_doc": 0.15,
            "audit_log": 0.10,
            "admin_override": 0.10,
        }.get(channel, 0.0)
        auth_weight = min(1.0, auth_weight + channel_boost)
    else:
        # provenance 未定義時の後方互換フォールバック
        if getattr(feedback, "human_rejected", False):
            auth_weight = 0.70
        elif getattr(feedback, "human_approved", False):
            auth_weight = 0.60
        else:
            auth_weight = 0.40

    # 2. 反証の実質性（具体的な対向命題・是正知識の提示）
    substance = 0.0
    if getattr(feedback, "correction_content", None):
        substance += 0.15
    if getattr(feedback, "new_knowledge_provided", None):
        substance += 0.10

    # 3. 問い・ドメインに対する管轄スコープ適合度 (Scope / Domain Relevance: BASE v2.0 §4.2)
    # 権限や拘束は絶対値ではなく「その問い・ドメインに対して強いか」で立ち上がる
    # 例: HR部門adminは人事(hr)には強いが、インフラ(security/infra)には管轄外
    scope_factor = 1.0
    authority_scope = getattr(prov, "authority_scope", None) if prov else None
    target_domain = None
    if snapshot and getattr(snapshot, "efp", None):
        target_domain = getattr(snapshot.efp, "category", None)
    elif snapshot and getattr(snapshot, "f_pred", None):
        target_domain = getattr(snapshot.f_pred, "domain", None)

    if authority_scope is not None and target_domain:
        norm_scope = authority_scope.lower().strip()
        norm_target = target_domain.lower().strip()
        if norm_scope in ("*", "__any__", "any", "global", "all"):
            scope_factor = 1.0
        elif norm_scope == norm_target:
            scope_factor = 1.0
        else:
            # 明示された管轄外への言及（他ドメインへの口出し）に対する拘束力減衰
            scope_factor = 0.5

    # 4. 言明タイプ (claim_type) と情報源の適合性
    # - fact (確定事実・操作記録): 監査ログ (audit) や公式決定で最大拘束 (1.0)
    # - judgment (裁量意見): 管理者/監査であっても主観的意見は事実言明より控えめに評価
    claim_type = getattr(prov, "claim_type", None) if prov else None
    claim_factor = 1.0
    if claim_type == "fact":
        if getattr(prov, "source_type", "") == "audit" or getattr(prov, "channel", "") in ("audit_log", "official_doc"):
            claim_factor = 1.05
    elif claim_type == "judgment":
        claim_factor = 0.80

    # 5. 時点拘束 (observed_at) の関係相対的な時間減衰 (Relation-dependent Freshness Decay)
    # 「古いから弱い」のではなく、「この関係の性質において時間経過がどの程度拘束を切るか」を評価
    # - historical_fact / event_record / fact (確定事実・取引記録・ログ): 過去に確定した事実の拘束力は半減期 ∞ (減衰なし)
    # - current_state / sensor_state / operational_state (動的現在状態・観測値): 事実であっても急速に失効 (半減期 1.0日)
    # - policy / institutional (制度・法的決定・基本規程): 半減期 730日 (2年)
    # - rule / procedural (マニュアル・通常規則): 半減期 90日 (標準)
    # - ephemeral / status (セッション・一時的状態): 半減期 3日
    time_factor = 1.0
    obs_at = getattr(prov, "observed_at", None) if prov else None
    if obs_at is not None:
        try:
            import math
            now = current_time or datetime.now(timezone.utc)
            if obs_at.tzinfo is None:
                obs_at = obs_at.replace(tzinfo=timezone.utc)
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            elapsed_days = max(0.0, (now - obs_at).total_seconds() / 86400.0)

            relation_type = getattr(prov, "relation_type", None) or claim_type
            if relation_type in ("historical_fact", "event_record", "fact"):
                half_life = float("inf")
            elif relation_type in ("current_state", "sensor_state", "operational_state"):
                half_life = 1.0    # 現在状態は 1日で半減
            elif relation_type in ("policy", "institutional", "legal"):
                half_life = 730.0  # 2年
            elif relation_type in ("ephemeral", "status"):
                half_life = 3.0    # 3日
            else:
                half_life = 90.0   # 標準 (rule / procedural)

            if math.isinf(half_life):
                time_factor = 1.0
            else:
                time_factor = math.exp(-math.log(2) * elapsed_days / half_life)
        except Exception:
            time_factor = 1.0

    # 制度的公式決定 (is_authoritative=True) の評価 (BASE v2.0 §4.2: 権限の無制限特権化の排除)
    # 1. 主観的裁量意見 (judgment): どんな権限者・媒体であっても上限 0.80
    # 2. 一般公式言明 (general): admin や official_doc であっても上限 0.90
    # 3. 現場事実報告 (factual_report): 制度制定権ではないため上限 0.90
    # 4. 一般照会 (general_inquiry): 上限 0.85
    # 5. 未知・未指定 (claim_type is None または target_relation is None):
    #    何を拘束する権限なのか不明（未回収関係 ξ が大きい）ため、admin であっても上限 0.90 に抑制
    # 6. 確定事実・制度制定・公式規則 (fact / rule / policy) かつ制度的決定権の正式行使 (rule_promulgation, approval_authority 等)
    #    かつ管轄内 (scope >= 1.0) かつ新鮮 (time >= 0.99) の場合のみ満額 1.0 を保証
    if prov and getattr(prov, "is_authoritative", False):
        target_rel = getattr(prov, "target_relation", None)
        if claim_type == "judgment":
            effective_score = auth_weight * scope_factor * claim_factor
            c_prime = min(0.80, effective_score * time_factor)
        elif claim_type == "general":
            effective_score = auth_weight * scope_factor * 0.90
            c_prime = min(0.90, effective_score * time_factor)
        elif target_rel == "factual_report":
            c_prime = min(0.90, auth_weight * scope_factor * 0.90 * time_factor)
        elif target_rel == "general_inquiry":
            c_prime = min(0.85, auth_weight * scope_factor * 0.85 * time_factor)
        elif claim_type is None or target_rel is None:
            # 言明タイプまたは関係種別が未指定（何についての権限行使か不明）:
            # 未回収関係 ξ が大きいため最大拘束 1.0 には到達させず上限 0.90 に抑制
            c_prime = min(0.90, auth_weight * scope_factor * 0.90 * time_factor)
        elif scope_factor >= 1.0 and time_factor >= 0.99:
            # 明示的言明タイプと制度的関係行使
            if (claim_type in ("fact", "rule", "policy") and
                target_rel in ("rule_promulgation", "approval_authority", "institutional_decision", "audit_record")):
                c_prime = 1.0
            else:
                effective_score = auth_weight * scope_factor * claim_factor
                c_prime = min(0.95, effective_score * time_factor)
        else:
            effective_score = auth_weight * scope_factor * claim_factor
            c_prime = min(1.0, effective_score * time_factor)
    else:
        effective_score = (auth_weight + substance) * scope_factor * claim_factor
        c_prime = min(1.0, effective_score * time_factor)

    return max(0.1, float(c_prime))


def compute_opposing_conflict_strength(
    c_old: float,
    c_prime: float,
    has_conflict: bool,
) -> float:
    """
    既存拘束 C_old と後続拘束 C_prime の衝突度合いに基づく実効対向拘束強度。
    BASE v2.0: C_old strong + C_prime strong + conflict (E > 0) のときに H を強く保持する。
    """
    if not has_conflict:
        return 1.0
    # 衝突時: C_old と C_prime がともに強いほど熱蓄積が大きくブーストされる
    # 最大で 1.0 + 1.0 * 1.0 * 2.0 = 3.0
    return max(1.0, 1.0 + (c_old * c_prime) * 2.0)


def is_support_node_eligible(
    support_node: object,
    query: str,
    cfg: ConstraintConfig,
    now: datetime,
) -> bool:
    """
    支援ノードが束の拘束力（synergy / survive）を支える健全な状態にあるかを個別検査。
    (1) freshness: rupture_freshness_threshold 未満なら除外（陳腐化ノードは支えられない）
    (2) rejection_ratio: rupture_rejection_ratio_threshold 以上なら除外（拒絶多数ノードは支えられない）
    (3) relevance: 問いへの適合度が極小（< 0.3）なら除外（無関係ノードは支えられない）
    """
    s_rel = _compute_relevance(query, support_node.trigger_pattern)
    if s_rel < 0.3:
        return False

    s_fresh = _compute_freshness(support_node.last_updated, cfg.freshness_half_life_days, now)
    if s_fresh < cfg.rupture_freshness_threshold:
        return False

    s_total = support_node.success_count + support_node.failure_count + support_node.rejection_count
    if s_total > 0:
        if (support_node.rejection_count / s_total) >= cfg.rupture_rejection_ratio_threshold:
            return False

    return True


# ---------------------------------------------------------------------------
# RelationConstraintLocator: 強い位置の特定
# ---------------------------------------------------------------------------

class RelationConstraintLocator:
    """
    現在の EFP（問い）と MBGraph から、拘束が強く働いている位置を特定する。

    特定する位置の種類：
      - "strong"   : constraint_score が高い（現在の問いに強く拘束が働いている）
      - "bridge"   : constraint_score は低くても、除去すると解釈域が大きく縮小する橋ノード
      - "authority": authority_level が高い（制度的に強い拘束）
      - "source"   : 承認比率が高い（ソース信頼性が高い）
    """

    def __init__(self, config: Optional[ConstraintConfig] = None):
        self.config = config or ConstraintConfig()

    def locate_bundle_for_node(
        self,
        mb_graph: object,
        node: object,
        ctx: ConstraintContext,
    ) -> ConstraintBundle:
        """
        推論カスケードのホットパス用：
        すでにマッチした単一ノードに対して局所的に ConstraintBundle を評価構築する (O(keys))。
        全ノード走査を回避し、推論の軽快さを維持する。
        """
        cfg = ctx.config if ctx.config is not None else self.config
        efp = ctx.efp
        query = efp.query_text
        now = ctx.current_time

        keys = node.trigger_pattern.get("exact_keys", [])
        rel = _compute_relevance(query, node.trigger_pattern)

        fresh = _compute_freshness(node.last_updated, cfg.freshness_half_life_days, now)
        auth = _compute_authority_weight(node.authority_level)
        src = _compute_source_strength(node.approval_count, node.rejection_count)
        conv = 0.5  # 局所評価時のニュートラル収束値
        score = _compute_constraint_score(rel, fresh, auth, src, conv, cfg)

        locus_type = "strong"
        if node.authority_level in ("require_approval", "human_only"):
            locus_type = "authority"
        elif src > 0.7:
            locus_type = "source"

        # 関連ノード（同一ドメイン内でトリガーキーを共有・支援する共起ルール群）を束ねる
        # BASE v2.0: 単一ノード属性ではなく「関係の束 (ConstraintBundle)」として拘束位置を表現
        candidate_supporting_nodes = []
        if hasattr(mb_graph, "find_co_occurring_nodes"):
            candidate_supporting_nodes = mb_graph.find_co_occurring_nodes(node, limit=4)
        elif hasattr(mb_graph, "list_nodes"):
            try:
                my_keys = set(k.strip().lower() for k in keys)
                if my_keys:
                    for other in mb_graph.list_nodes(domain=node.domain):
                        if other.id != node.id:
                            other_keys = set(k.strip().lower() for k in other.trigger_pattern.get("exact_keys", []))
                            if other_keys & my_keys:
                                candidate_supporting_nodes.append(other)
                                if len(candidate_supporting_nodes) >= 4:
                                    break
            except Exception:
                pass

        # 【健全かつ同一方向の支援ノードの層別化 (Explicit vs Inferred Support: BASE v2.0 §4.2)】
        # 1. 支援ノード自身の健全性検査（陳腐化・拒絶多数・無関係ノードは除外）
        # 2. 関係性・整合性検査（明示的 contradict / independent の除外、payload 極性矛盾の除外）
        # 3. レイヤー分離:
        #    - effective_supporting_nodes: 明示的 support (確定支援ノード → bundle.node_ids)
        #    - inferred_supporting_nodes: 暗黙 inferred_support (推論支援ノード → bundle.inferred_node_ids, auxiliary/ξ)
        effective_supporting_nodes = []
        inferred_supporting_nodes = []
        for s in candidate_supporting_nodes:
            if not is_support_node_eligible(s, query, cfg, now):
                continue
            n_rel = _check_node_relation(node, s)
            if n_rel == "support":
                effective_supporting_nodes.append(s)
            elif n_rel == "inferred_support":
                inferred_supporting_nodes.append(s)

        bundle_node_ids = [node.id] + [s.id for s in effective_supporting_nodes]
        inferred_node_ids = [s.id for s in inferred_supporting_nodes]

        # 束としての総合拘束強度 (Bundle Constraint Score: BASE v2.0 §4.2)
        # 代表ノード単体だけでなく、束に含まれる健全な支援ノード群がどれだけ強固に裏付けているかを相乗評価
        synergy_boost = 0.0
        seen_lineages = set()
        primary_lineage = getattr(node, "source_lineage", None) or getattr(node, "source_id", None)
        if primary_lineage:
            seen_lineages.add(primary_lineage)

        # 1. 確定支援ノード群による確定相乗効果 (Core Synergy: rel_factor = 1.0)
        core_synergy = 0.0
        for s in effective_supporting_nodes:
            s_rel = _compute_relevance(query, s.trigger_pattern)
            s_src = _compute_source_strength(s.approval_count, s.rejection_count)
            s_lineage = getattr(s, "source_lineage", None) or getattr(s, "source_id", None)
            if s_lineage and s_lineage in seen_lineages:
                lineage_factor = 0.15
            else:
                lineage_factor = 1.0
                if s_lineage:
                    seen_lineages.add(s_lineage)
            core_synergy += 0.04 * s_rel * s_src * lineage_factor * 1.0

        core_synergy = min(0.12, core_synergy)
        core_score = min(1.0, score + core_synergy)

        # 2. 推論支援ノード群による潜在シグナル (Auxiliary Signal: rel_factor = 0.5)
        # BASE v2.0: ξ は観測候補として記録されるが、確定した拘束強度には直接加えない
        aux_signal = 0.0
        for s in inferred_supporting_nodes:
            s_rel = _compute_relevance(query, s.trigger_pattern)
            s_src = _compute_source_strength(s.approval_count, s.rejection_count)
            s_lineage = getattr(s, "source_lineage", None) or getattr(s, "source_id", None)
            if s_lineage and s_lineage in seen_lineages:
                lineage_factor = 0.15
            else:
                lineage_factor = 1.0
                if s_lineage:
                    seen_lineages.add(s_lineage)
            aux_signal += 0.04 * s_rel * s_src * lineage_factor * 0.5

        aux_signal = min(0.12, aux_signal)
        core_conv = min(1.0, conv + 0.05 * len(effective_supporting_nodes))
        aux_conv_signal = min(0.5, 0.02 * len(inferred_supporting_nodes))

        return ConstraintBundle(
            node_ids=bundle_node_ids,
            locus_type=locus_type,
            constraint_score=core_score,
            relevance=rel,
            freshness=fresh,
            authority_weight=auth,
            source_strength=src,
            convergence=core_conv,
            is_structural_bridge=False,
            inferred_node_ids=inferred_node_ids,
            auxiliary_constraint_signal=aux_signal,
            auxiliary_convergence_signal=aux_conv_signal,
        )

    def locate_bundle_for_locus(
        self,
        mb_graph: object,
        locus_node_ids: List[str],
        ctx: ConstraintContext,
    ) -> Optional[ConstraintBundle]:
        r"""
        責任拘束位置群 (constraint_locus_ids) から拘束束を特定・構築する (BASE v2.0 §4.2)。
        - 単一ノード (Level 0〜2): locate_bundle_for_node に委譲。
        - 複数ノード (Level 3): locus_node_ids 内の各ノードの拘束強度を局所評価し、
          最も強く働いている代表ノード（および支援関係）を核として ConstraintBundle を構築する。
        - locus_node_ids 内の全ノードを bundle に包含し、破断検査において M_B \ bundle の実効的切断を可能にする。
        """
        if not locus_node_ids:
            return None

        # 存在ノードの収集
        nodes = []
        for nid in locus_node_ids:
            n = mb_graph.get(nid) if hasattr(mb_graph, "get") else None
            if n is not None:
                nodes.append(n)

        if not nodes:
            return None

        if len(nodes) == 1:
            return self.locate_bundle_for_node(mb_graph, nodes[0], ctx)

        # 複数ノード (Level 3 等): 責任拘束位置 L 全体を対象とする複合束を構築 (BASE v2.0 §4.2)
        # 【拘束強度評価と破断切断範囲の 1:1 一致】
        # score評価対象 = L, cut切断対象 = L
        cfg = ctx.config if ctx.config is not None else self.config
        efp = ctx.efp
        query = efp.query_text
        now = ctx.current_time

        evaluated_nodes = []
        seen_lineages = set()
        locus_ids = [n.id for n in nodes]

        for n in nodes:
            rel = _compute_relevance(query, n.trigger_pattern)
            fresh = _compute_freshness(n.last_updated, cfg.freshness_half_life_days, now)
            auth = _compute_authority_weight(n.authority_level)
            src = _compute_source_strength(n.approval_count, n.rejection_count)
            base_score = _compute_constraint_score(rel, fresh, auth, src, 0.5, cfg)
            lineage = getattr(n, "source_lineage", None) or getattr(n, "source_id", None)
            evaluated_nodes.append((base_score, rel, fresh, auth, src, lineage, n))

        evaluated_nodes.sort(key=lambda x: x[0], reverse=True)
        primary = evaluated_nodes[0]
        primary_score, primary_rel, primary_fresh, primary_auth, primary_src, primary_lin, primary_node = primary
        if primary_lin:
            seen_lineages.add(primary_lin)

        # L 内部の関係分離 (BASE v2.0 §4.2):
        # - core_node_ids: primary + 明示的 support (確定拘束ノード)
        # - inferred_node_ids: inferred_support (未回収関係 ξ)
        # - contradict / unknown: core からは完全に除外
        core_node_ids = [primary_node.id]
        inferred_node_ids = []

        # L 内部の相互支援・相乗効果 (Composite Synergy)
        # 【公理的厳格分離】:
        # core_synergy_mass: 明示的 support のみから加算 (core constraint score へ寄与)
        # aux_synergy_signal: inferred_support 由来 (auxiliary ξ signal として保持)
        core_synergy_mass = 0.0
        aux_synergy_signal = 0.0

        for score, rel, fresh, auth, src, lin, n in evaluated_nodes[1:]:
            rel_type = _check_node_relation(primary_node, n)
            lin_factor = 0.2 if (lin and lin in seen_lineages) else 1.0
            if lin:
                seen_lineages.add(lin)

            if rel_type == "support":
                core_node_ids.append(n.id)
                core_synergy_mass += 0.03 * rel * src * lin_factor
            elif rel_type == "inferred_support":
                inferred_node_ids.append(n.id)
                aux_synergy_signal += 0.02 * rel * src * lin_factor
            # contradict や unknown は core にも auxiliary にも入れず除外

        core_synergy_mass = min(0.20, core_synergy_mass)
        aux_signal = min(0.50, aux_synergy_signal + 0.05 * len(inferred_node_ids))
        core_score = min(1.0, primary_score + core_synergy_mass)

        # 独立ソース系統数に基づく収束度
        core_conv = min(1.0, 0.4 + 0.15 * len(seen_lineages))
        aux_conv = min(0.5, 0.02 * len(inferred_node_ids))

        return ConstraintBundle(
            node_ids=core_node_ids,  # primary + 明示的 support のみ
            locus_type="strong" if core_score >= 0.6 else "subgraph",
            constraint_score=core_score,
            relevance=primary_rel,
            freshness=primary_fresh,
            authority_weight=primary_auth,
            source_strength=primary_src,
            convergence=core_conv,
            is_structural_bridge=False,
            inferred_node_ids=inferred_node_ids,  # auxiliary ξ
            auxiliary_constraint_signal=aux_signal,
            auxiliary_convergence_signal=aux_conv,
        )

    def locate(
        self,
        mb_graph: object,     # MBGraph
        ctx: ConstraintContext,
    ) -> List[ConstraintBundle]:
        """
        現在の EFP および ConstraintContext のもとで、
        拘束が強く働いている ConstraintBundle のリストを返す (一括 O(N) 評価)。
        """
        from collections import Counter

        all_nodes = mb_graph.list_nodes()
        efp = ctx.efp
        query = efp.query_text
        domain = ctx.active_domain if ctx.active_domain is not None else efp.category
        now = ctx.current_time
        cfg = ctx.config if ctx.config is not None else self.config

        bundles: List[ConstraintBundle] = []

        # ドメインごとの action_type 出現頻度を O(N) で事前集計
        domain_type_counts: Dict[str, Counter] = {}
        for n in all_nodes:
            d = n.domain or "general"
            if d not in domain_type_counts:
                domain_type_counts[d] = Counter()
            domain_type_counts[d][n.action_template.get("type", "")] += 1

        # --- 各ノードの拘束スコアを計算 ---
        node_scores: Dict[str, float] = {}
        node_details: Dict[str, dict] = {}

        for node in all_nodes:
            keys = node.trigger_pattern.get("exact_keys", [])
            rel = _compute_relevance(query, node.trigger_pattern)

            fresh = _compute_freshness(node.last_updated, cfg.freshness_half_life_days, now)
            auth = _compute_authority_weight(node.authority_level)
            src = _compute_source_strength(node.approval_count, node.rejection_count)

            # convergence を事前集計から O(1) で算出
            d_counter = domain_type_counts.get(node.domain or "general", Counter())
            total_d = sum(d_counter.values()) - 1
            if total_d > 0:
                my_type = node.action_template.get("type", "")
                matches = d_counter.get(my_type, 1) - 1
                conv = matches / total_d
            else:
                conv = 0.5

            score = _compute_constraint_score(rel, fresh, auth, src, conv, cfg)

            node_scores[node.id] = score
            node_details[node.id] = {
                "relevance": rel,
                "freshness": fresh,
                "authority_weight": auth,
                "source_strength": src,
                "convergence": conv,
            }

        # --- ドメインフィルタ（境界 B の適用） ---
        def domain_eligible(node) -> bool:
            if not domain or domain in ("*", "__any__", "any"):
                return True
            return node.domain == domain

        eligible_nodes = [n for n in all_nodes if domain_eligible(n)]

        # --- (a) strong / authority / source ロケスの特定 ---
        for node in eligible_nodes:
            score = node_scores.get(node.id, 0.0)
            d = node_details.get(node.id, {})
            locus_type = "strong"

            if node.authority_level in ("require_approval", "human_only"):
                locus_type = "authority"
            elif d.get("source_strength", 0) > 0.7:
                locus_type = "source"

            if score > 0.0:
                candidate_supporting_nodes = []
                if hasattr(mb_graph, "find_co_occurring_nodes"):
                    candidate_supporting_nodes = mb_graph.find_co_occurring_nodes(node, limit=4)
                else:
                    my_keys = set(k.strip().lower() for k in node.trigger_pattern.get("exact_keys", []))
                    if my_keys:
                        for other in eligible_nodes:
                            if other.id != node.id:
                                other_keys = set(k.strip().lower() for k in other.trigger_pattern.get("exact_keys", []))
                                if other_keys & my_keys:
                                    candidate_supporting_nodes.append(other)
                                    if len(candidate_supporting_nodes) >= 4:
                                        break

                effective_supporting_nodes = []
                inferred_supporting_nodes = []
                for s in candidate_supporting_nodes:
                    if not is_support_node_eligible(s, query, cfg, now):
                        continue
                    n_rel = _check_node_relation(node, s)
                    if n_rel == "support":
                        effective_supporting_nodes.append(s)
                    elif n_rel == "inferred_support":
                        inferred_supporting_nodes.append(s)

                bundle_node_ids = [node.id] + [s.id for s in effective_supporting_nodes]
                inferred_node_ids = [s.id for s in inferred_supporting_nodes]

                synergy_boost = 0.0
                seen_lineages = set()
                primary_lineage = getattr(node, "source_lineage", None) or getattr(node, "source_id", None)
                if primary_lineage:
                    seen_lineages.add(primary_lineage)

                core_synergy = 0.0
                for s in effective_supporting_nodes:
                    s_rel = _compute_relevance(query, s.trigger_pattern)
                    s_src = _compute_source_strength(s.approval_count, s.rejection_count)

                    s_lineage = getattr(s, "source_lineage", None) or getattr(s, "source_id", None)
                    if s_lineage and s_lineage in seen_lineages:
                        lineage_factor = 0.15
                    else:
                        lineage_factor = 1.0
                        if s_lineage:
                            seen_lineages.add(s_lineage)

                    core_synergy += 0.04 * s_rel * s_src * lineage_factor * 1.0

                core_synergy = min(0.12, core_synergy)
                core_score = min(1.0, score + core_synergy)

                aux_signal = 0.0
                for s in inferred_supporting_nodes:
                    s_rel = _compute_relevance(query, s.trigger_pattern)
                    s_src = _compute_source_strength(s.approval_count, s.rejection_count)

                    s_lineage = getattr(s, "source_lineage", None) or getattr(s, "source_id", None)
                    if s_lineage and s_lineage in seen_lineages:
                        lineage_factor = 0.15
                    else:
                        lineage_factor = 1.0
                        if s_lineage:
                            seen_lineages.add(s_lineage)

                    aux_signal += 0.04 * s_rel * s_src * lineage_factor * 0.5

                aux_signal = min(0.12, aux_signal)

                core_conv = min(1.0, d.get("convergence", 0.0) + 0.05 * len(effective_supporting_nodes))
                aux_conv_signal = min(0.5, 0.02 * len(inferred_supporting_nodes))

                bundles.append(ConstraintBundle(
                    node_ids=bundle_node_ids,
                    locus_type=locus_type,
                    constraint_score=core_score,
                    relevance=d.get("relevance", 0.0),
                    freshness=d.get("freshness", 0.0),
                    authority_weight=d.get("authority_weight", 0.0),
                    source_strength=d.get("source_strength", 0.0),
                    convergence=core_conv,
                    is_structural_bridge=False,
                    inferred_node_ids=inferred_node_ids,
                    auxiliary_constraint_signal=aux_signal,
                    auxiliary_convergence_signal=aux_conv_signal,
                ))

        # --- (b) 構造的橋の高速検出 (O(M * keys)) ---
        key_freq = Counter()
        for n in eligible_nodes:
            for k in n.trigger_pattern.get("exact_keys", []):
                key_freq[_normalize(k)] += 1

        total_unique_keys = len(key_freq)
        if total_unique_keys > 0:
            for node in eligible_nodes:
                # このノードだけが持っている一意のキーの数を数える
                exclusive_keys = sum(
                    1 for k in node.trigger_pattern.get("exact_keys", [])
                    if key_freq[_normalize(k)] == 1
                )
                coverage_drop = exclusive_keys / total_unique_keys
                if coverage_drop >= cfg.bridge_coverage_drop_threshold:
                    existing = next((b for b in bundles if b.primary_node_id() == node.id), None)
                    if existing:
                        existing.locus_type = "bridge"
                        existing.is_structural_bridge = True
                    else:
                        d = node_details.get(node.id, {})
                        bundles.append(ConstraintBundle(
                            node_ids=[node.id],
                            locus_type="bridge",
                            constraint_score=node_scores.get(node.id, 0.1),
                            relevance=d.get("relevance", 0.0),
                            freshness=d.get("freshness", 0.0),
                            authority_weight=d.get("authority_weight", 0.0),
                            source_strength=d.get("source_strength", 0.0),
                            convergence=d.get("convergence", 0.0),
                            is_structural_bridge=True,
                        ))

        bundles.sort(key=lambda b: b.constraint_score, reverse=True)
        return bundles


# ---------------------------------------------------------------------------
# RuptureProbe: 破断検査 (Perturbation Probe)
# ---------------------------------------------------------------------------

class RuptureProbe:
    """
    特定した ConstraintBundle が現在の構造を実際に支えているかを検査する。

    BASE v2.0 公理:
      「強い = 正しい」ではない。
      破断検査とは「対象を揺らし、関係を外し、境界を広げても、なお維持されるか」の検査である。

    【判定規律 (B4/B5)】:
      - break      : 明確な赤信号（時間減衰・反証拒絶）または摂動で競合・破綻が露出
      - survive    : 摂動に耐え、かつ十分な承認実績または制度的権限によって維持された
      - unresolved : 未検査、または摂動に対する耐性が未確認（安易に survive と呼ばない）
    """

    def __init__(self, config: Optional[ConstraintConfig] = None):
        self.config = config or ConstraintConfig()

    def probe(
        self,
        bundle: ConstraintBundle,
        mb_graph: object,     # MBGraph (更新前の frozen_mb であること)
        ctx: ConstraintContext,
    ) -> RuptureResult:
        """
        ConstraintBundle に対して破断検査を実施し RuptureResult を返す。
        opposing_strength: 反証拘束の強さ（add_heat の重みとして使用）
        """
        cfg = ctx.config if ctx.config is not None else self.config
        nid = bundle.primary_node_id()
        node = mb_graph.get(nid) if nid else None

        supporting_nodes = []
        for sid in bundle.supporting_node_ids:
            sn = mb_graph.get(sid)
            if sn is not None:
                supporting_nodes.append(sn)

        # =============================================================
        # Phase 1: 実効的切断摂動と変化量測定 (Rupture Effect: BASE v2.0 §4.2)
        # =============================================================
        # 「この束を切断したとき、現在の解釈可能域がどう変わるか」
        # F_base = interp(M_B, EFP, C0) vs F_cut = interp(M_B \ bundle, EFP, C0)
        # 【最優先: 独立した2つの Cascade インスタンスを生成し、同一 C0 から独立推論】
        # F_base 形成によるキャッシュ更新 (C0 -> C1) が F_cut に一切伝播しないことを保証
        rupture_effect: Optional[float] = None
        f_base = None
        f_without = None
        conditions_hash: Optional[str] = None
        base_view_hash: Optional[str] = None
        cut_view_hash: Optional[str] = None
        intervention_verified: bool = False
        base_trace_id: Optional[str] = None
        cut_trace_id: Optional[str] = None
        effect_verified_bundle_ids: List[str] = []
        effect_verified_node_ids: List[str] = []

        def _make_result(verdict: str, opposing_strength: float, rupture_reason: str = "") -> RuptureResult:
            return RuptureResult(
                bundle=bundle,
                verdict=verdict,
                opposing_strength=opposing_strength,
                rupture_reason=rupture_reason,
                rupture_effect=rupture_effect,
                conditions_hash=conditions_hash,
                base_mb_view_hash=base_view_hash,
                cut_mb_view_hash=cut_view_hash,
                intervention_verified=intervention_verified,
                base_trace_id=base_trace_id,
                cut_trace_id=cut_trace_id,
                effect_verified_bundle_ids=list(effect_verified_bundle_ids),
                effect_verified_node_ids=list(effect_verified_node_ids),
                effect_verified_locus_ids=list(effect_verified_bundle_ids),
            )

        try:
            if ctx.frozen_context is not None and hasattr(ctx.frozen_context, "create_isolated_cascade"):
                cascade_base = ctx.frozen_context.create_isolated_cascade()
                cascade_cut = ctx.frozen_context.create_isolated_cascade()
            else:
                from rdl_enterprise.cascade import InterpCascade, CascadeConfig
                bridge_to_use = ctx.llm_bridge
                if bridge_to_use is None and ctx.frozen_context is not None:
                    bridge_to_use = getattr(ctx.frozen_context, "llm_bridge", None)
                cascade_base = InterpCascade(
                    mb_graph,
                    llm_bridge=bridge_to_use,
                    config=CascadeConfig(),
                    constraint_config=cfg,
                    constraint_evaluation_time=ctx.current_time,
                )
                cascade_cut = InterpCascade(
                    mb_graph,
                    llm_bridge=bridge_to_use,
                    config=CascadeConfig(),
                    constraint_config=cfg,
                    constraint_evaluation_time=ctx.current_time,
                )
            # 外生固定条件集合 K (ReplayToken) の初期化・キャプチャ
            # 【最優先】事後採取ではなく、実際の事前予測 F を生んだ外生固定条件 K_actual を再利用
            bridge = getattr(cascade_base, "llm_bridge", None)
            replay_token_K = None
            if getattr(ctx, "actual_replay_token", None) is not None:
                replay_token_K = ctx.actual_replay_token
            elif ctx.frozen_context is not None and getattr(ctx.frozen_context, "actual_replay_token", None) is not None:
                replay_token_K = ctx.frozen_context.actual_replay_token
            elif bridge is not None:
                if hasattr(bridge, "capture_counterfactual_context") and callable(bridge.capture_counterfactual_context):
                    try:
                        replay_token_K = bridge.capture_counterfactual_context(ctx.efp)
                    except Exception:
                        replay_token_K = None
                elif hasattr(bridge, "create_replay_token") and callable(bridge.create_replay_token):
                    try:
                        replay_token_K = bridge.create_replay_token()
                    except Exception:
                        replay_token_K = None
                elif _is_deterministic_replay_capable(bridge):
                    from rdl_enterprise.snapshot import LLMBridgeIdentity
                    ident = LLMBridgeIdentity.from_bridge(bridge)
                    replay_token_K = ident.create_replay_token()

            # F_base と F_cut を同一の固定条件 K の下で評価
            f_base = cascade_base.interpret(ctx.efp, skip_constraint_boost=True, replay_token=replay_token_K)

            # f_base から token が得られた場合はそれを後続に伝播
            if replay_token_K is None and getattr(f_base, "replay_token", None) is not None:
                replay_token_K = f_base.replay_token

            f_without = cascade_cut.interpret(
                ctx.efp,
                exclude_node_ids=bundle.node_ids,
                skip_constraint_boost=True,
                replay_token=replay_token_K,
            )

            # Level 3 で外部推論器 (llm_bridge) が呼び出された場合:
            used_llm_bridge = bridge is not None and (
                (f_base is not None and f_base.cost_tier == 3) or
                (f_without is not None and f_without.cost_tier == 3)
            )

            # 監査証跡情報の抽出 (BASE v2.0 §4.2)
            base_meta = getattr(f_base, "metadata", {}) if f_base else {}
            cut_meta = getattr(f_without, "metadata", {}) if f_without else {}
            base_verified = base_meta.get("counterfactual_verified", False)
            cut_verified = cut_meta.get("counterfactual_verified", False)
            base_view_hash = base_meta.get("mb_view_hash", None)
            cut_view_hash = cut_meta.get("mb_view_hash", None)
            conditions_hash = getattr(replay_token_K, "conditions_hash", None) if replay_token_K else None

            base_trace = base_meta.get("interpretation_trace", None)
            cut_trace = cut_meta.get("interpretation_trace", None)
            base_trace_id = getattr(base_trace, "trace_id", None) if base_trace else None
            cut_trace_id = getattr(cut_trace, "trace_id", None) if cut_trace else None

            base_exec_trace = base_meta.get("execution_trace", None)
            cut_exec_trace = cut_meta.get("execution_trace", None)

            # 【暗号論的 4点照合契約 (Applied View Hash Contract: BASE v2.0 §4.2)】
            # 1. base_exec_trace.requested == applied == base_view_hash
            # 2. cut_exec_trace.requested == applied == cut_view_hash
            # 3. base_view_hash != cut_view_hash
            # 4. base_conditions_hash == cut_conditions_hash == conditions_hash_K
            hashes_match = False
            if base_exec_trace and cut_exec_trace and base_view_hash and cut_view_hash:
                cond_k = conditions_hash or ""
                cond_base = getattr(base_exec_trace, "conditions_hash", "")
                cond_cut = getattr(cut_exec_trace, "conditions_hash", "")
                conds_match = (cond_base == cond_cut == cond_k) if cond_k else (cond_base == cond_cut)
                hashes_match = (
                    base_exec_trace.requested_mb_view_hash == base_exec_trace.applied_mb_view_hash == base_view_hash
                    and cut_exec_trace.requested_mb_view_hash == cut_exec_trace.applied_mb_view_hash == cut_view_hash
                    and base_view_hash != cut_view_hash
                    and conds_match
                )

            # 介入実証フラグの厳格判定 (BASE v2.0 §4.2):
            # 捏造バイパスの完全排除 (Self-Exception Prohibition):
            # Bridge が applied_mb_view_hash / execution_trace を明示報告し、
            # 4 点照合 (requested == applied == view_hash, base != cut, conds_match) を満たした場合のみ
            # intervention_verified = True とする。
            # execution_trace が存在しない場合は実証不能として Fail-Closed (False) とする。
            if base_exec_trace is not None and cut_exec_trace is not None:
                intervention_verified = bool(hashes_match)
            else:
                intervention_verified = False

            if used_llm_bridge:
                # 1. リプレイ能力・反実仮想契約の検証
                if not _is_deterministic_replay_capable(bridge):
                    rupture_effect = None
                else:
                    # 2. 外生条件同一性の実証と内生的変化の測定 (BASE v2.0: Counterfactual Replay Contract)
                    if f_base and f_without and f_base.cost_tier == 3 and f_without.cost_tier == 3:
                        is_mb_dependent = (
                            getattr(bridge, "is_mb_dependent", False)
                            or hasattr(bridge, "build_prompt_with_mb")
                            or hasattr(bridge, "resolve_counterfactual")
                        )
                        if not is_mb_dependent:
                            # M_B に依存しない固定質問の場合、同一 K であれば完全に同一出力となるべき
                            if (f_base.action_type != f_without.action_type or
                                f_base.content != f_without.content or
                                abs(f_base.confidence - f_without.confidence) > 1e-4):
                                # 同一条件・同一入力なのに出力が揺らいだ（外生揺らぎの漏洩） -> 測定不能 (None = ξ)
                                rupture_effect = None
                            else:
                                # 外生条件同一性が実証され、かつ M_B 切断による影響もなし
                                rupture_effect = 0.0
                        else:
                            # M_B 依存プロンプト/介入の場合:
                            # 【厳格な反実仮想介入検証 (Strict Intervention Verification: BASE v2.0 §4.2)】
                            # 自己申告 (is_mb_dependent=True) 単体でのバイパスを完全排除。
                            # 実際に CounterfactualInput を受領・適用した実証 (intervention_verified=True または received_counterfactual_input)
                            # がない場合、介入が行われていないのに rupture_effect を算出することを禁止する (None = ξ)
                            if not intervention_verified and not getattr(bridge, "received_counterfactual_input", False):
                                rupture_effect = None
                            else:
                                diff = 0.0
                                if f_base.action_type != f_without.action_type:
                                    diff += 0.4
                                if f_base.content != f_without.content:
                                    diff += 0.4
                                diff += 0.2 * abs(f_base.confidence - f_without.confidence)
                                rupture_effect = min(1.0, round(diff, 4))
                                if rupture_effect > 0:
                                    # 束集合レベル検証 (set-level verification: BASE v2.0 §4.2)
                                    effect_verified_bundle_ids = list(bundle.node_ids)
                                    # 単一ノード束ならそのノード自体が個別効果を持つ
                                    if len(bundle.node_ids) == 1:
                                        effect_verified_node_ids = list(bundle.node_ids)
                                    else:
                                        # 複数ノード束の場合の単一ノード摂動 (Single-Node Ablation / Leave-One-Out)
                                        for single_nid in bundle.node_ids:
                                            try:
                                                f_single_cut = cascade_cut.interpret(
                                                    ctx.efp,
                                                    exclude_node_ids=[single_nid],
                                                    skip_constraint_boost=True,
                                                    replay_token=replay_token_K,
                                                )
                                                s_diff = 0.0
                                                if f_base.action_type != f_single_cut.action_type:
                                                    s_diff += 0.4
                                                if f_base.content != f_single_cut.content:
                                                    s_diff += 0.4
                                                s_diff += 0.2 * abs(f_base.confidence - f_single_cut.confidence)
                                                if round(s_diff, 4) > 0:
                                                    effect_verified_node_ids.append(single_nid)
                                            except Exception:
                                                pass
                    else:
                        # 片方が Level 3 でもう片方がローカル階層（切断によってフォールバック等が発生）
                        diff = 0.0
                        if f_base and f_without:
                            if f_base.action_type != f_without.action_type:
                                diff += 0.4
                            if f_base.matched_node_id != f_without.matched_node_id:
                                diff += 0.3
                            diff += 0.2 * abs(f_base.confidence - f_without.confidence)
                            if f_base.content != f_without.content:
                                diff += 0.1
                        rupture_effect = min(1.0, round(diff, 4))
                        if rupture_effect > 0:
                            effect_verified_bundle_ids = list(bundle.node_ids)
                            effect_verified_node_ids = list(bundle.node_ids)
            else:
                # ローカル決定的推論 (Level 0 - Level 2、または bridge なしの決定的フォールバック):
                # 決定的な再実行による変化量測定
                diff = 0.0
                if f_base and f_without:
                    if f_base.action_type != f_without.action_type:
                        diff += 0.4
                    if f_base.matched_node_id != f_without.matched_node_id:
                        diff += 0.3
                    diff += 0.2 * abs(f_base.confidence - f_without.confidence)
                    if f_base.content != f_without.content:
                        diff += 0.1
                rupture_effect = min(1.0, round(diff, 4))
                if rupture_effect > 0:
                    effect_verified_bundle_ids = list(bundle.node_ids)
                    effect_verified_node_ids = list(bundle.node_ids)
        except Exception:
            rupture_effect = None
            base_view_hash = None
            cut_view_hash = None
            conditions_hash = None
            intervention_verified = False
            base_trace_id = None
            cut_trace_id = None
            effect_verified_bundle_ids = []
            effect_verified_node_ids = []

        # =============================================================
        # Phase 2: 破断・妥当性判定 (Verdict Probing: survive / break / unresolved)
        # =============================================================

        # -------------------------------------------------------------
        # 0. 束内部の関係対立・亀裂検査 (Internal Fissure Probing)
        # -------------------------------------------------------------
        # 束を構成する支援ノードと代表ノードとの関係性を _check_node_relation で評価。
        # 明示的な関係を優先し、文字列完全一致ではなく関係モデルに基づいて亀裂を検出する。
        # - contradict: 破断 (break)
        # - support / inferred_support 以外 (independent, unknown 等): 保留 (unresolved / ξ)
        if node is not None and supporting_nodes:
            for sn in supporting_nodes:
                rel = _check_node_relation(node, sn)
                if rel == "contradict":
                    return _make_result(
                        verdict="break",
                        opposing_strength=1.5,
                        rupture_reason=f"束内部における対立・矛盾関係（ノード {sn.id} との対立・破綻）",
                    )
                elif rel != "support":
                    return _make_result(
                        verdict="unresolved",
                        opposing_strength=0.8,
                        rupture_reason=f"束内部における関係の未確定（ノード {sn.id} との関係: {rel}、ξ として残存）",
                    )

        # -------------------------------------------------------------
        # 1. 赤信号検査（時間減衰・反証拒絶による直接破断）
        # -------------------------------------------------------------
        if bundle.freshness < cfg.rupture_freshness_threshold:
            return _make_result(
                verdict="break",
                opposing_strength=1.0 + (1.0 - bundle.freshness),
                rupture_reason=f"freshness 低下による破断 (freshness={bundle.freshness:.3f} < {cfg.rupture_freshness_threshold})",
            )

        if node is not None:
            total = node.success_count + node.failure_count + node.rejection_count
            if total > 0:
                rejection_ratio = node.rejection_count / total
                if rejection_ratio >= cfg.rupture_rejection_ratio_threshold:
                    opposing = 1.0 + rejection_ratio
                    return _make_result(
                        verdict="break",
                        opposing_strength=opposing,
                        rupture_reason=f"rejection 比率超過による破断 (ratio={rejection_ratio:.2f} >= {cfg.rupture_rejection_ratio_threshold})",
                    )

        # 構造的橋だがソースが極端に弱い場合（単一障害点かつ根拠薄弱）
        if bundle.is_structural_bridge and bundle.source_strength < 0.3:
            return _make_result(
                verdict="unresolved",
                opposing_strength=0.5,
                rupture_reason="構造的橋だがソース拘束が弱い（未回収関係 ξ として保持）",
            )

        # -------------------------------------------------------------
        # 2. 摂動検査 (Perturbation: 境界拡張による他ドメイン潜在競合の炙り出し)
        # -------------------------------------------------------------
        if node is not None and ctx.active_domain and ctx.active_domain not in ("*", "__any__", "any"):
            all_nodes = mb_graph.list_nodes()
            ctx_expanded = ConstraintContext(
                efp=ctx.efp,
                current_time=ctx.current_time,
                mb_version=ctx.mb_version,
                active_domain="*",
                config=cfg,
            )
            locator = RelationConstraintLocator(cfg)
            node_bundle_any = locator.locate_bundle_for_node(mb_graph, node, ctx_expanded)

            for other in all_nodes:
                if other.id == node.id or other.domain == node.domain:
                    continue
                common_keys = set(k.lower() for k in node.trigger_pattern.get("exact_keys", [])) & \
                              set(k.lower() for k in other.trigger_pattern.get("exact_keys", []))
                if common_keys:
                    if other.action_template.get("type") != node.action_template.get("type") or \
                       other.action_template.get("payload") != node.action_template.get("payload"):
                        other_bundle_any = locator.locate_bundle_for_node(mb_graph, other, ctx_expanded)
                        if other_bundle_any.constraint_score >= node_bundle_any.constraint_score:
                            return _make_result(
                                verdict="break",
                                opposing_strength=1.5,
                                rupture_reason=(
                                    f"境界拡張摂動により他ドメイン({other.domain})の競合拘束"
                                    f"({other.id}: C_rel={other_bundle_any.constraint_score:.2f} >= "
                                    f"{node_bundle_any.constraint_score:.2f})が露出"
                                ),
                            )

        # -------------------------------------------------------------
        # 2.5 実効的バンドル切断による潜在対向解釈露出検査 (Break on Exposure)
        # -------------------------------------------------------------
        if (f_without and f_base and
            f_without.matched_node_id and
            f_without.matched_node_id not in bundle.node_ids and
            f_without.action_type != f_base.action_type and
            f_without.confidence >= f_base.confidence):
            return _make_result(
                verdict="break",
                opposing_strength=1.8,
                rupture_reason=(
                    f"実効的バンドル切断(M_B \\ bundle)により、対向解釈ノード "
                    f"({f_without.matched_node_id}: conf={f_without.confidence:.2f} >= {f_base.confidence:.2f}) "
                    f"との潜在衝突が露出して破断"
                ),
            )

        # -------------------------------------------------------------
        # 3. 生存判定 (Survive)
        # -------------------------------------------------------------
        # 支援ノードの承認実績を根拠とする場合、明示的 support のみを有効とし、
        # 暗黙の推論関係 (inferred_support) 単独での survive は認めず ξ として慎重に保持する
        eligible_supporting_nodes = [
            sn for sn in supporting_nodes
            if is_support_node_eligible(sn, ctx.efp.query_text, cfg, ctx.current_time)
            and (node is not None and _check_node_relation(node, sn) == "support")
        ]
        has_proven_track_record = (
            (node is not None and node.approval_count >= cfg.min_survive_approvals) or
            bundle.authority_weight >= 0.8 or
            (eligible_supporting_nodes and any(s.approval_count >= cfg.min_survive_approvals for s in eligible_supporting_nodes))
        )
        if has_proven_track_record and bundle.relevance >= cfg.min_survive_relevance:
            return _make_result(
                verdict="survive",
                opposing_strength=0.0,
                rupture_reason="",
            )

        # -------------------------------------------------------------
        # 4. デフォルト: 未検査・耐性未確認 (Unresolved)
        # -------------------------------------------------------------
        return _make_result(
            verdict="unresolved",
            opposing_strength=0.5,
            rupture_reason="十分な承認実績・摂動耐性の未確認による保留（ξ として残存）",
        )
