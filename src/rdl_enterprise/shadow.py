"""
RDL Enterprise: Shadow Execution & Counterfactual Comparison
現行本番 M_B の「観測事実 (Observed Fact)」と、再編候補 M_B' の「反実仮想推定 (Counterfactual Estimate)」を
突き合わせ、三者比較 (F_prod vs F_shadow vs EFP'_prod) を行う。

RDL認識論の原則:
- 本番 M_B の誤差は、実際に外界へ作用して得られた「観測された誤差 (prod_observed_error)」。
- 候補 M_B' の誤差は、提示されなかった世界に対する「反実仮想的な推定値 (shadow_counterfactual_error_estimate)」。
- 観測事例が不足している場合は passed=True ではなく evaluation_status="insufficient_evidence" とする。
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

from .mb_graph import MBGraph
from .cascade import InterpCascade
from .snapshot import BusinessInput, InterpretationPrediction, FeedbackResult, CaseStatus


@dataclass
class ShadowPredictionPair:
    """入力 EFP に対する本番予測(実績)とシャドウ予測(反実仮想)の比較ペア"""
    ticket_id: str
    efp: BusinessInput
    prod_pred: InterpretationPrediction      # 実際に本番が生成・実行した予測 F_prod
    shadow_pred: InterpretationPrediction    # 候補 M_B' が並行生成した予測 F_shadow
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
    """
    実結果 EFP'_prod が到着した際の三者比較
    (本番予測 F_prod vs 候補予測 F_shadow vs 観測事実 EFP'_prod)
    """
    ticket_id: str
    prediction_pair: ShadowPredictionPair
    feedback: FeedbackResult
    prod_observed_error: float                         # 実際に観測された本番誤差 E_prod (Observed Fact)
    shadow_counterfactual_error_estimate: float        # 「もし新回答を出していたら」の反実仮想誤差推定 E_shadow (Counterfactual)
    prod_status: CaseStatus                            # 本番の実績ステータス
    shadow_counterfactual_status: CaseStatus           # 候補の反実仮想ステータス
    resolved_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    @property
    def is_improved(self) -> bool:
        """反実仮想推定において、新予測の方が誤差が小さい (改善見込み)"""
        return self.shadow_counterfactual_error_estimate < self.prod_observed_error

    @property
    def is_regressed(self) -> bool:
        """反実仮想推定において、新予測の方が誤差が大きい (退行・改悪の危険)"""
        return self.shadow_counterfactual_error_estimate > self.prod_observed_error


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
    evaluation_status: str                   # "passed" | "failed" | "insufficient_evidence"
    passed: bool                             # 承認可能か (evaluation_status == "passed")
    triplet_details: List[Dict[str, Any]] = field(default_factory=list)


class ShadowEvaluator:
    """
    シャドウ実行エンジン:
    現行本番 MBGraph と候補 MBGraph を保持し、実トラフィックに対する
    並行推論と反実仮想三者比較を集計する
    """
    def __init__(
        self,
        proposal_id: str,
        prod_mb: MBGraph,
        candidate_mb: MBGraph,
        max_allowed_regression_rate: float = 0.05,
        minimum_resolved_cases: int = 1,     # 合否判定に必要な最小解決事例数
    ):
        self.proposal_id = proposal_id
        self.prod_cascade = InterpCascade(prod_mb)
        self.shadow_cascade = InterpCascade(candidate_mb)
        self.max_allowed_regression_rate = max_allowed_regression_rate
        self.minimum_resolved_cases = minimum_resolved_cases

        self.pending_pairs: Dict[str, ShadowPredictionPair] = {}
        self.resolved_triplets: List[ShadowResolutionTriplet] = []

    def evaluate_input(
        self,
        efp: BusinessInput,
        prod_pred: Optional[InterpretationPrediction] = None,
    ) -> ShadowPredictionPair:
        """
        入力 EFP に対し、本番推論結果 (実績) と候補推論 (シャドウ) をペア化する。
        prod_pred が渡された場合は二重推論を避け、実際に本番が採用した出力をそのまま使用する。
        """
        actual_prod_pred = prod_pred if prod_pred is not None else self.prod_cascade.interpret(efp)
        shadow_pred = self.shadow_cascade.interpret(efp)

        pair = ShadowPredictionPair(
            ticket_id=efp.ticket_id,
            efp=efp,
            prod_pred=actual_prod_pred,
            shadow_pred=shadow_pred,
        )
        self.pending_pairs[efp.ticket_id] = pair
        return pair

    def record_feedback(
        self,
        ticket_id: str,
        feedback: FeedbackResult,
    ) -> Optional[ShadowResolutionTriplet]:
        """事後フィードバック EFP' による反実仮想三者比較の確定"""
        if ticket_id not in self.pending_pairs:
            return None

        pair = self.pending_pairs.pop(ticket_id)

        # 1. 実際に観測された本番誤差 E_prod (Observed Fact)
        if feedback.human_rejected or not feedback.user_resolved:
            prod_err = 1.0
            prod_status = CaseStatus.FAILURE if not feedback.human_rejected else CaseStatus.REJECTED
        elif feedback.user_resolved:
            prod_err = 0.0
            prod_status = CaseStatus.SUCCESS
        else:
            prod_err = 0.5
            prod_status = CaseStatus.UNKNOWN

        # 2. 候補 M_B' の反実仮想誤差推定 E_shadow (Counterfactual Estimate)
        shadow_err = prod_err
        shadow_status = prod_status

        if feedback.new_knowledge_provided:
            # ユーザー・人間が示した新知識が、候補 M_B' の回答にすでに含まれていた場合 -> 解決していたはず (誤差 0.0)
            if feedback.new_knowledge_provided in pair.shadow_pred.content:
                shadow_err = 0.0
                shadow_status = CaseStatus.SUCCESS
            elif pair.shadow_pred.cost_tier <= pair.prod_pred.cost_tier and not feedback.human_rejected:
                shadow_err = min(prod_err, 0.2)
        elif feedback.user_resolved and not feedback.human_rejected:
            # 本番で正常解決した場合、候補のコスト階層を比較
            if pair.shadow_pred.cost_tier < pair.prod_pred.cost_tier:
                shadow_err = 0.0
            elif pair.shadow_pred.cost_tier > pair.prod_pred.cost_tier:
                shadow_err = 0.1

        triplet = ShadowResolutionTriplet(
            ticket_id=ticket_id,
            prediction_pair=pair,
            feedback=feedback,
            prod_observed_error=prod_err,
            shadow_counterfactual_error_estimate=shadow_err,
            prod_status=prod_status,
            shadow_counterfactual_status=shadow_status,
        )
        self.resolved_triplets.append(triplet)
        return triplet

    def generate_report(self) -> ShadowReport:
        """集計比較レポートを生成 (証拠不十分 insufficient_evidence を厳格に判定)"""
        total = len(self.resolved_triplets)

        # 最小解決件数に満たない場合は証拠不十分 (passed=False)
        if total < self.minimum_resolved_cases:
            return ShadowReport(
                proposal_id=self.proposal_id,
                total_shadow_cases=total + len(self.pending_pairs),
                resolved_triplets_count=total,
                improved_count=0,
                regressed_count=0,
                unchanged_count=0,
                tier_improved_count=0,
                avg_confidence_delta=0.0,
                regression_rate=0.0,
                evaluation_status="insufficient_evidence",
                passed=False,
            )

        improved = sum(1 for t in self.resolved_triplets if t.is_improved)
        regressed = sum(1 for t in self.resolved_triplets if t.is_regressed)
        unchanged = total - improved - regressed
        tier_improved = sum(1 for t in self.resolved_triplets if t.prediction_pair.tier_delta > 0)
        avg_conf = sum(t.prediction_pair.confidence_delta for t in self.resolved_triplets) / total
        reg_rate = regressed / total

        is_passed = (reg_rate <= self.max_allowed_regression_rate)
        eval_status = "passed" if is_passed else "failed"

        details = [
            {
                "ticket_id": t.ticket_id,
                "category": t.prediction_pair.efp.category,
                "query": t.prediction_pair.efp.query_text,
                "prod_tier": t.prediction_pair.prod_pred.cost_tier,
                "shadow_tier": t.prediction_pair.shadow_pred.cost_tier,
                "prod_observed_error": t.prod_observed_error,
                "shadow_counterfactual_error_estimate": t.shadow_counterfactual_error_estimate,
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
            evaluation_status=eval_status,
            passed=is_passed,
            triplet_details=details,
        )