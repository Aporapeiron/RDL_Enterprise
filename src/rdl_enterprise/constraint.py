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


def _compute_convergence(node_id: str, domain: str, all_nodes: list) -> float:
    """
    同一 domain 内の他ノードと「同じ action type」を返す割合を収束一致として使用。
    """
    domain_nodes = [n for n in all_nodes if n.domain == domain and n.id != node_id]
    if not domain_nodes:
        return 0.5  # 比較対象なしはニュートラル
    node = next((n for n in all_nodes if n.id == node_id), None)
    if not node:
        return 0.5
    my_type = node.action_template.get("type", "")
    matches = sum(1 for n in domain_nodes if n.action_template.get("type", "") == my_type)
    return matches / len(domain_nodes)


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

    def locate(
        self,
        mb_graph: object,     # MBGraph
        ctx: ConstraintContext,
    ) -> List[ConstraintBundle]:
        """
        現在の EFP および ConstraintContext のもとで、
        拘束が強く働いている ConstraintBundle のリストを返す。
        """
        all_nodes = mb_graph.list_nodes()
        efp = ctx.efp
        query = efp.query_text
        domain = efp.category
        now = ctx.current_time
        cfg = ctx.config if ctx.config is not None else self.config

        bundles: List[ConstraintBundle] = []

        # --- 各ノードの拘束スコアを計算 ---
        node_scores: Dict[str, float] = {}
        node_details: Dict[str, dict] = {}

        for node in all_nodes:
            # relevance: query と exact_keys の最高 Jaccard 類似度
            keys = node.trigger_pattern.get("exact_keys", [])
            rel = max((_bigram_jaccard(query, k) for k in keys), default=0.0)

            # rule_expr がある場合も加点
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
            conv = _compute_convergence(node.id, node.domain, all_nodes)
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

            # authority ロケス（authority_level が require_approval 以上）
            if node.authority_level in ("require_approval", "human_only"):
                locus_type = "authority"
            # source ロケス（承認比率が高い）
            elif d.get("source_strength", 0) > 0.7:
                locus_type = "source"

            if score > 0.0:  # score > 0 のノードはすべて候補として登録
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

        # --- (b) 構造的橋の検出 ---
        # ノードを除いたとき、eligible なクエリへの応答可能率が大きく落ちるか判定
        # （簡易実装：exact_keys でカバー可能な問い集合の縮小を見る）
        all_keys = set()
        for n in eligible_nodes:
            for k in n.trigger_pattern.get("exact_keys", []):
                all_keys.add(_normalize(k))

        for node in eligible_nodes:
            remaining_keys = set()
            for n in eligible_nodes:
                if n.id == node.id:
                    continue
                for k in n.trigger_pattern.get("exact_keys", []):
                    remaining_keys.add(_normalize(k))
            if not all_keys:
                continue
            coverage_drop = (len(all_keys) - len(remaining_keys)) / len(all_keys)
            if coverage_drop >= cfg.bridge_coverage_drop_threshold:
                # bridge として追加（既存エントリを更新または新規追加）
                existing = next((b for b in bundles if b.node_ids == [node.id]), None)
                if existing:
                    # 既存エントリを bridge に昇格
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

        # constraint_score 降順でソート
        bundles.sort(key=lambda b: b.constraint_score, reverse=True)
        return bundles


# ---------------------------------------------------------------------------
# RuptureProbe: 破断検査
# ---------------------------------------------------------------------------

class RuptureProbe:
    """
    特定した ConstraintBundle が現在の構造を実際に支えているかを検査する。

    BASE v2.0: 強い = 正しい、ではない。
    「強く支えているか」の検査であり、破断すれば E として蓄積する。

    破断操作（Phase 1 実装）：
      1. 時間検査（freshness < threshold → break）
      2. rejection 比率検査（差し戻しが多いなら break 候補）
      3. それ以外は unresolved

    verdict:
      "survive"    → F の confidence boost に使用
      "break"      → opposing_strength で E を重みづけ
      "unresolved" → ξ へ残す
    """

    def __init__(self, config: Optional[ConstraintConfig] = None):
        self.config = config or ConstraintConfig()

    def probe(
        self,
        bundle: ConstraintBundle,
        mb_graph: object,     # MBGraph
        ctx: ConstraintContext,
    ) -> RuptureResult:
        """
        ConstraintBundle に対して破断検査を実施し RuptureResult を返す。
        opposing_strength: 反証拘束の強さ（E の重みづけに使用）
        """
        cfg = ctx.config if ctx.config is not None else self.config
        nid = bundle.primary_node_id()
        node = mb_graph.get(nid) if nid else None

        # 1. 時間破断検査（freshness が極端に低い）← 最優先：他の拘束断面より前に判定
        if bundle.freshness < cfg.rupture_freshness_threshold:
            return RuptureResult(
                bundle=bundle,
                verdict="break",
                opposing_strength=1.0 + (1.0 - bundle.freshness),  # 古いほど opposing が強い
                rupture_reason=f"freshness 低下による破断 (freshness={bundle.freshness:.3f} < {cfg.rupture_freshness_threshold})",
            )

        # 2. rejection 比率による破断検査
        if node is not None:
            total = node.success_count + node.failure_count + node.rejection_count
            if total > 0:
                rejection_ratio = node.rejection_count / total
                if rejection_ratio >= cfg.rupture_rejection_ratio_threshold:
                    opposing = 1.0 + rejection_ratio  # 差し戻し比率が高いほど opposing が強い
                    return RuptureResult(
                        bundle=bundle,
                        verdict="break",
                        opposing_strength=opposing,
                        rupture_reason=f"rejection 比率超過による破断 (ratio={rejection_ratio:.2f} >= {cfg.rupture_rejection_ratio_threshold})",
                    )

        # 3. 構造的橋でかつ freshness / source が弱い場合 → unresolved
        #    （freshness 検査を通過した場合のみ評価される）
        if bundle.is_structural_bridge and bundle.source_strength < 0.3:
            return RuptureResult(
                bundle=bundle,
                verdict="unresolved",
                opposing_strength=0.5,
                rupture_reason="構造的橋だがソース拘束が弱い（ξ として残存）",
            )

        # 4. 通過（survive）
        return RuptureResult(
            bundle=bundle,
            verdict="survive",
            opposing_strength=0.0,  # 衝突なし
            rupture_reason="",
        )
