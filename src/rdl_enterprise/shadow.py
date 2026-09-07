"""
RDL Enterprise: Shadow Execution & Triplet Comparison
現行本番 M_B と再編候補 M_B' を並行推論 (Shadow Execution) させ、
事後フィードバック EFP' 到着時に「旧予測 vs 新予測 vs 実結果」の三者比較を行う。
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

from .mb_graph import MBGraph
from .cascade import InterpCascade
from .snapshot import BusinessInput, InterpretationPrediction, FeedbackResult, CaseStatus


@dataclass
class ShadowPredictionPair:
    """入力 EFP に対する本番予測とシャドウ予測の比較ペア"""
    ticket_id: str
    efp: BusinessInput
    prod_pred: InterpretationPrediction      # 現行本番 M_B の予測 F
    shadow_pred: InterpretationPrediction    # 候補 M_B' の予測 F_shadow
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    @property
    def node_changed(self) -> bool:
        return self.prod_pred.matched_node_id != self.shadow_pred.matched_node_id

    @property
    def tier_delta(self) -> int:
        """シャドウでのコスト変化 (正ならシャドウの方が低コスト・改善)"""
        return self.prod_pred.cost_tier - self.shadow_pred.cost_tier

    @property
    def confidence_delta(self) -> float:
        """確信度の変化 (F_shadow - F_prod)"""
        return self.shadow_pred.confidence - self.prod_pred.confidence

    @property
    def content_changed(self) -> bool:
        return self.prod_pred.content != self.shadow_pred.content


@dataclass
class ShadowResolutionTriplet:
    """実結果 EFP' が到着した際の三者比較 (旧予測 vs 新予測 vs 実結果)"""
    ticket_id: str
    prediction_pair: ShadowPredictionPair
    feedback: FeedbackResult
    prod_pred_error: float                   # 旧予測の誤差 E_prod
    shadow_pred_error: float                 # 新予測の誤差 E_shadow
    prod_status: CaseStatus                  # 旧予測ベースの成否
    shadow_status: CaseStatus                # 新予測ベースの成否
    resolved_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    @property
    def is_improved(self) -> bool:
        """新予測の方が誤差が小さい (精度改善)"""
        return self.shadow_pred_error < self.prod_pred_error

    @property
    def is_regressed(self) -> bool:
        """新予測の方が誤差が大きい (改悪・退行)"""
        return self.shadow_pred_error > self.prod_pred_error


@dataclass
class ShadowReport:
    """シャドウ並行運用の集計評価レポート"""
    proposal_id: str
    total_shadow_cases: int
    resolved_triplets_count: int
    improved_count: int
    regressed_count: int
    unchanged_count: int
    tier_improved_count: int                 # コスト短縮された件数
    avg_confidence_delta: float
    regression_rate: float                   # 改悪率 [0.0, 1.0]
    passed: bool                             # 承認基準 (改悪率が許容範囲内か)
    triplet_details: List[Dict[str, Any]] = field(default_factory=list)


class ShadowEvaluator:
    """
    シャドウ実行エンジン:
    現行本番 MBGraph と候補 MBGraph を保持し、並走推論と三者比較を集計する
    """
    def __init__(
        self,
        proposal_id: str,
        prod_mb: MBGraph,
        candidate_mb: MBGraph,
        max_allowed_regression_rate: float = 0.05,
    ):
        self.proposal_id = proposal_id
        self.prod_cascade = InterpCascade(prod_mb)
        self.shadow_cascade = InterpCascade(candidate_mb)
        self.max_allowed_regression_rate = max_allowed_regression_rate

        self.pending_pairs: Dict[str, ShadowPredictionPair] = {}
        self.resolved_triplets: List[ShadowResolutionTriplet] = []

    def evaluate_input(self, efp: BusinessInput) -> ShadowPredictionPair:
        """入力 EFP を本番と候補の両方に投入してシャドウ推論"""
        prod_pred = self.prod_cascade.interpret(efp)
        shadow_pred = self.shadow_cascade.interpret(efp)

        pair = ShadowPredictionPair(
            ticket_id=efp.ticket_id,
            efp=efp,
            prod_pred=prod_pred,
            shadow_pred=shadow_pred,
        )
        self.pending_pairs[efp.ticket_id] = pair
        return pair

    def record_feedback(
        self,
        ticket_id: str,
        feedback: FeedbackResult,
    ) -> Optional[ShadowResolutionTriplet]:
        """事後フィードバック EFP' による三者比較の確定"""
        if ticket_id not in self.pending_pairs:
            return None

        pair = self.pending_pairs.pop(ticket_id)

        # 1. 旧予測の誤差 E_prod を算出
        if feedback.human_rejected or not feedback.user_resolved:
            prod_err = 1.0
            prod_status = CaseStatus.FAILURE if not feedback.human_rejected else CaseStatus.REJECTED
        elif feedback.user_resolved:
            prod_err = 0.0
            prod_status = CaseStatus.SUCCESS
        else:
            prod_err = 0.5
            prod_status = CaseStatus.UNKNOWN

        # 2. 新予測の誤差 E_shadow を算出
        # 新ノードの回答がフィードバックの提示知識と一致しているか、またはユーザー満足を満たすか
        shadow_err = prod_err  # デフォルトは同等
        shadow_status = prod_status

        if feedback.new_knowledge_provided:
            # 新知識が提供された案件で、候補 M_B' の出力がすでに新知識を含んでいれば解決 (誤差ゼロ)
            if feedback.new_knowledge_provided in pair.shadow_pred.content:
                shadow_err = 0.0
                shadow_status = CaseStatus.SUCCESS
            elif pair.shadow_pred.cost_tier <= pair.prod_pred.cost_tier and not feedback.human_rejected:
                shadow_err = min(prod_err, 0.2)
        elif feedback.user_resolved and not feedback.human_rejected:
            # 正常解決の場合、シャドウの確信度やコストを考慮
            if pair.shadow_pred.cost_tier < pair.prod_pred.cost_tier:
                shadow_err = 0.0  # より低コストで同一解決
            elif pair.shadow_pred.cost_tier > pair.prod_pred.cost_tier:
                shadow_err = 0.1  # コスト悪化は軽微なペナルティ

        triplet = ShadowResolutionTriplet(
            ticket_id=ticket_id,
            prediction_pair=pair,
            feedback=feedback,
            prod_pred_error=prod_err,
            shadow_pred_error=shadow_err,
            prod_status=prod_status,
            shadow_status=shadow_status,
        )
        self.resolved_triplets.append(triplet)
        return triplet

    def generate_report(self) -> ShadowReport:
        """集計比較レポートを生成"""
        total = len(self.resolved_triplets)
        if total == 0:
            return ShadowReport(
                proposal_id=self.proposal_id,
                total_shadow_cases=len(self.pending_pairs),
                resolved_triplets_count=0,
                improved_count=0,
                regressed_count=0,
                unchanged_count=0,
                tier_improved_count=0,
                avg_confidence_delta=0.0,
                regression_rate=0.0,
                passed=True,
            )

        improved = sum(1 for t in self.resolved_triplets if t.is_improved)
        regressed = sum(1 for t in self.resolved_triplets if t.is_regressed)
        unchanged = total - improved - regressed
        tier_improved = sum(1 for t in self.resolved_triplets if t.prediction_pair.tier_delta > 0)
        avg_conf = sum(t.prediction_pair.confidence_delta for t in self.resolved_triplets) / total
        reg_rate = regressed / total

        passed = (reg_rate <= self.max_allowed_regression_rate)

        details = [
            {
                "ticket_id": t.ticket_id,
                "category": t.prediction_pair.efp.category,
                "query": t.prediction_pair.efp.query_text,
                "prod_tier": t.prediction_pair.prod_pred.cost_tier,
                "shadow_tier": t.prediction_pair.shadow_pred.cost_tier,
                "prod_error": t.prod_pred_error,
                "shadow_error": t.shadow_pred_error,
                "is_improved": t.is_improved,
                "is_regressed": t.is_regressed,
            }
            for t in self.resolved_triplets
        ]

        return ShadowReport(
            proposal_id=self.proposal_id,
            total_shadow_cases=total + len(self.pending_pairs),
            resolved_triplets_count=total,
            improved_count=improved,
            regressed_count=regressed,
            unchanged_count=unchanged,
            tier_improved_count=tier_improved,
            avg_confidence_delta=avg_conf,
            regression_rate=reg_rate,
            passed=passed,
            triplet_details=details,
        )