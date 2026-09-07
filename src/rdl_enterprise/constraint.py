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
from typing import List, Optional, Dict
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


# ---------------------------------------------------------------------------
# ConstraintBundle: 拘束束（単一ノードではなく関係の束）
# ---------------------------------------------------------------------------

@dataclass
class ConstraintBundle:
    """
    「関係の束」として表現した拘束位置。
    強く支えている位置 または 切れると全体が変わる構造的橋 を表す。

    BASE v2.0: 強い場所とは「そこを外す・反転する・揺らすと、
    現在の解釈可能域が大きく変わる場所」。
    """
    node_ids: List[str]           # 束を構成するノード群（複数可）
    locus_type: str               # "strong" | "bridge" | "authority" | "source"
    constraint_score: float       # 現在の問い・時点・位置における拘束強度 ∈ [0, 1]

    # 各拘束断面の値（内訳）
    relevance: float = 0.0        # 現在の問いへの適合
    freshness: float = 0.0        # 時間的新鮮さ
    authority_weight: float = 0.0 # 制度的権限
    source_strength: float = 0.0  # ソース拘束（承認比率）
    convergence: float = 0.0      # 複数独立関係の収束一致

    is_structural_bridge: bool = False  # 構造的に唯一の接続橋か

    def primary_node_id(self) -> Optional[str]:
        """代表ノード ID（最初の要素）"""
        return self.node_ids[0] if self.node_ids else None


# ---------------------------------------------------------------------------
# RuptureResult: 破断検査の結果
# ---------------------------------------------------------------------------

@dataclass
class RuptureResult:
    """
    RuptureProbe による破断検査の結果。
    BASE v2.0: 強い = 正しい、ではなく「現在の構造を支えているか」を問う。
    """
    bundle: ConstraintBundle
    verdict: str              # "survive" | "break" | "unresolved"
    opposing_strength: float  # 反証拘束の強さ（add_heat の重みとして使用）
    rupture_reason: str = ""


# ---------------------------------------------------------------------------
# 内部ユーティリティ
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text.lower())


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
      - 管理者・監査権限 (source_type="admin" / "audit", authority_level="human_only"): 0.90 ~ 0.95
      - 先輩・上級権限 (source_type="senior", authority_level="require_approval"): 0.85
      - 伝達経路 (official_doc, audit_log, admin_override): チャネル加算
      - 反証の実質性 (correction_content, new_knowledge_provided): 具現性加算
      - 時点拘束 (observed_at): 報告時刻の新鮮さによる時間的減衰
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
            auth_weight = 0.85
        elif getattr(feedback, "human_approved", False):
            auth_weight = 0.70
        else:
            auth_weight = 0.40

    # 2. 反証の実質性（具体的な対向命題・是正知識の提示）
    substance = 0.0
    if getattr(feedback, "correction_content", None):
        substance += 0.15
    if getattr(feedback, "new_knowledge_provided", None):
        substance += 0.10

    # 3. 時点拘束 (observed_at の新鮮さ)
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
            time_factor = math.exp(-math.log(2) * elapsed_days / 90.0)
        except Exception:
            time_factor = 1.0

    c_prime = min(1.0, (auth_weight + substance) * time_factor)
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
        rel = max((_bigram_jaccard(query, k) for k in keys), default=0.0)
        rule_expr = node.trigger_pattern.get("rule_expr")
        if rule_expr:
            try:
                if re.search(rule_expr, query, re.IGNORECASE):
                    rel = max(rel, 0.8)
            except re.error:
                pass

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

        return ConstraintBundle(
            node_ids=[node.id],
            locus_type=locus_type,
            constraint_score=score,
            relevance=rel,
            freshness=fresh,
            authority_weight=auth,
            source_strength=src,
            convergence=conv,
            is_structural_bridge=False,
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
        domain = efp.category
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
            rel = max((_bigram_jaccard(query, k) for k in keys), default=0.0)

            rule_expr = node.trigger_pattern.get("rule_expr")
            if rule_expr:
                try:
                    if re.search(rule_expr, query, re.IGNORECASE):
                        rel = max(rel, 0.8)
                except re.error:
                    pass

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
                bundles.append(ConstraintBundle(
                    node_ids=[node.id],
                    locus_type=locus_type,
                    constraint_score=score,
                    relevance=d.get("relevance", 0.0),
                    freshness=d.get("freshness", 0.0),
                    authority_weight=d.get("authority_weight", 0.0),
                    source_strength=d.get("source_strength", 0.0),
                    convergence=d.get("convergence", 0.0),
                    is_structural_bridge=False,
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
                    existing = next((b for b in bundles if b.node_ids == [node.id]), None)
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

        # -------------------------------------------------------------
        # 1. 赤信号検査（時間減衰・反証拒絶による直接破断）
        # -------------------------------------------------------------
        if bundle.freshness < cfg.rupture_freshness_threshold:
            return RuptureResult(
                bundle=bundle,
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
                    return RuptureResult(
                        bundle=bundle,
                        verdict="break",
                        opposing_strength=opposing,
                        rupture_reason=f"rejection 比率超過による破断 (ratio={rejection_ratio:.2f} >= {cfg.rupture_rejection_ratio_threshold})",
                    )

        # 構造的橋だがソースが極端に弱い場合（単一障害点かつ根拠薄弱）
        if bundle.is_structural_bridge and bundle.source_strength < 0.3:
            return RuptureResult(
                bundle=bundle,
                verdict="unresolved",
                opposing_strength=0.5,
                rupture_reason="構造的橋だがソース拘束が弱い（未回収関係 ξ として保持）",
            )

        # -------------------------------------------------------------
        # 2. 摂動検査 (Perturbation: 境界拡張による潜在競合の炙り出し)
        # -------------------------------------------------------------
        # ドメイン境界 B をワイルドカードに拡張して、同一クエリに対して
        # 他ドメインにより強い関係拘束 (C_rel) を持つ異なる結論が存在しないかを検査
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
                # 他ドメインで同じキーを持っているか
                common_keys = set(k.lower() for k in node.trigger_pattern.get("exact_keys", [])) & \
                              set(k.lower() for k in other.trigger_pattern.get("exact_keys", []))
                if common_keys:
                    # 異なるアクションを提案しているか
                    if other.action_template.get("type") != node.action_template.get("type") or \
                       other.action_template.get("payload") != node.action_template.get("payload"):
                        # 【BASE v2.0 整合】confidence ではなく関係拘束強度 C_rel で競合判定！
                        other_bundle_any = locator.locate_bundle_for_node(mb_graph, other, ctx_expanded)
                        if other_bundle_any.constraint_score >= node_bundle_any.constraint_score:
                            return RuptureResult(
                                bundle=bundle,
                                verdict="break",
                                opposing_strength=1.5,
                                rupture_reason=(
                                    f"境界拡張摂動により他ドメイン({other.domain})の競合拘束"
                                    f"({other.id}: C_rel={other_bundle_any.constraint_score:.2f} >= "
                                    f"{node_bundle_any.constraint_score:.2f})が露出"
                                ),
                            )

        # -------------------------------------------------------------
        # 3. 生存判定 (Survive)
        # -------------------------------------------------------------
        # 摂動に耐え、十分な承認実績または制度的権限を持ち、現在の問いに適合している場合のみ survive
        has_proven_track_record = (
            (node is not None and node.approval_count >= cfg.min_survive_approvals) or
            bundle.authority_weight >= 0.8
        )
        if has_proven_track_record and bundle.relevance >= cfg.min_survive_relevance:
            return RuptureResult(
                bundle=bundle,
                verdict="survive",
                opposing_strength=0.0,
                rupture_reason="",
            )

        # -------------------------------------------------------------
        # 4. デフォルト: 未検査・耐性未確認 (Unresolved)
        # -------------------------------------------------------------
        # 安易に survive と呼ばず、未回収関係 ξ として扱う
        return RuptureResult(
            bundle=bundle,
            verdict="unresolved",
            opposing_strength=0.5,
            rupture_reason="十分な承認実績・摂動耐性の未確認による保留（ξ として残存）",
        )
