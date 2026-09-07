from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from .mb_graph import MBGraph, MBNode
from .h_state import HState
from .snapshot import BusinessInput, InterpretationPrediction, FeedbackResult, CaseSnapshot
from .cascade import InterpCascade
from .human import HumanQuery

@dataclass
class TicketExecutionResult:
    ticket_id: str
    prediction: InterpretationPrediction
    hitl_required: bool
    hitl_reason: str
    action_taken: str
    final_output: str
    e_prediction: float
    e_input: float
    current_h: float
    current_theta_eff: float
    transition_to_m_delta: bool
    cost_tier: int


class EnterpriseRuntime:
    """
    RDL業務AI ランタイムコア
    通常運転（巡航代謝）と再編相 M_Δ のライフサイクルを司る
    """
    def __init__(
        self,
        mb_graph: Optional[MBGraph] = None,
        theta_0: float = 2.0,
        gamma: float = 0.05,
        llm_bridge: Optional[Any] = None,
    ):
        self.mb_graph = mb_graph or MBGraph()
        self.h_state = HState(theta_0=theta_0, gamma=gamma)
        self.cascade = InterpCascade(self.mb_graph, llm_bridge=llm_bridge)
        self.human = HumanQuery()

        # 運用メトリクス
        self.processed_tickets_count = 0
        self.auto_resolved_count = 0
        self.hitl_count = 0
        self.m_delta_count = 0
        self.cost_tier_counts = {0: 0, 1: 0, 2: 0, 3: 0}
        self.history_snapshots: List[CaseSnapshot] = []

    def handle_ticket(
        self,
        efp: BusinessInput,
        feedback: Optional[FeedbackResult] = None,
        human_override_answer: Optional[str] = None,
    ) -> TicketExecutionResult:
        """
        1件の業務チケットを処理し、代謝サイクルを1巡させる
        """
        self.processed_tickets_count += 1

        # 1. 多層カスケード推論 (EFP -> F)
        pred = self.cascade.interpret(efp)
        self.cost_tier_counts[pred.cost_tier] = self.cost_tier_counts.get(pred.cost_tier, 0) + 1

        # スナップショット作成
        snapshot = CaseSnapshot(efp, pred)

        # 2. 人間問い合わせ (HITL) ゲート判定
        matched_node = self.mb_graph.get(pred.matched_node_id) if pred.matched_node_id else None
        hitl_eval = self.human.evaluate(efp, pred, matched_node)
        hitl_required = hitl_eval["must_ask"]
        hitl_reason = hitl_eval["reason"]

        if hitl_required:
            self.hitl_count += 1

        # 3. 行動実行（または人間介入による回答補正）
        if hitl_required and human_override_answer:
            action_taken = "human_assisted"
            final_output = human_override_answer
            # 人間が正解を授けた場合、これを沈澱（学習）させる
            self.cascade.crystallize_rule(efp, human_override_answer, efp.category or "general", approved=True)
        else:
            action_taken = pred.action_type
            final_output = pred.content

        # 4. 事後結果 EFP' の評価と差分 E の算出
        if not feedback:
            # デフォルトフィードバック（人間介入があれば成功、なければ自動処理成功と仮定）
            feedback = FeedbackResult(
                user_resolved=True,
                human_approved=bool(human_override_answer),
                human_rejected=False,
            )

        e_pred, e_input = snapshot.record_feedback(feedback)
        self.history_snapshots.append(snapshot)

        # 5. 熱 H の蓄積と散逸
        target_nid = pred.matched_node_id or "__unmatched__"
        self.h_state.add_heat(target_nid, pred_err=e_pred, input_err=e_input)
        self.h_state.record_observation(
            unclassified=(pred.matched_node_id is None),
            missing_info=(e_input > 0),
            unknown_input=(pred.cost_tier == 3),
            rejected=feedback.human_rejected,
        )

        # 自然散逸
        inertias = {nid: n.inertia() for nid, n in self.mb_graph.nodes.items()}
        self.h_state.dissipate(inertias)

        # 6. 閾値判定 (H >= θ_eff)
        should_leap, hot_node, current_h = self.h_state.should_leap(pred.matched_node_id)
        current_theta = self.h_state.theta_eff()

        transition_m_delta = False

        if should_leap:
            # 再編相 M_Δ へ移行！
            self.m_delta_count += 1
            transition_m_delta = True
            self._execute_m_delta_reorganization(hot_node, efp, feedback)
        else:
            # 通常運転：局所更新 (dM_B/dt)
            if matched_node:
                if feedback.user_resolved and not feedback.human_rejected:
                    matched_node.record_success(approved=feedback.human_approved)
                else:
                    matched_node.record_failure(rejected=feedback.human_rejected)

            if not hitl_required and feedback.user_resolved:
                self.auto_resolved_count += 1

        return TicketExecutionResult(
            ticket_id=efp.ticket_id,
            prediction=pred,
            hitl_required=hitl_required,
            hitl_reason=hitl_reason,
            action_taken=action_taken,
            final_output=final_output,
            e_prediction=e_pred,
            e_input=e_input,
            current_h=current_h,
            current_theta_eff=current_theta,
            transition_to_m_delta=transition_m_delta,
            cost_tier=pred.cost_tier,
        )

    def _execute_m_delta_reorganization(self, hot_node_id: str, efp: BusinessInput, feedback: FeedbackResult):
        """
        高負荷再編相 M_Δ
        熱が溜まったノードを対象化・解体・再編し、新ルール M_B' を適応。
        再編後は H_remaining を引き継ぐ。
        """
        node = self.mb_graph.get(hot_node_id)
        if node:
            # 差し戻しや苦情が多発した既存ノードを改訂
            if feedback.new_knowledge_provided:
                node.action_template["payload"] = feedback.new_knowledge_provided
            node.confidence = 0.6  # 再編されたため初期化
            node.failure_count = 0
            node.rejection_count = 0
            node.success_count = 1

        # 再編後の残存熱処理 (H_remaining: 20% の残存熱を残して引き継ぐ)
        self.h_state.apply_remaining_heat_after_leap(hot_node_id, remaining_ratio=0.2)

    def get_metrics(self) -> Dict[str, Any]:
        """運用メトリクスのサマリー"""
        total = max(1, self.processed_tickets_count)
        return {
            "total_tickets": self.processed_tickets_count,
            "auto_resolution_rate": self.auto_resolved_count / total,
            "hitl_rate": self.hitl_count / total,
            "m_delta_transitions": self.m_delta_count,
            "cost_tier_distribution": {k: v / total for k, v in self.cost_tier_counts.items()},
            "current_theta_eff": self.h_state.theta_eff(),
            "average_kappa": self.mb_graph.average_kappa(),
            "total_inertia": self.mb_graph.total_inertia(),
        }
