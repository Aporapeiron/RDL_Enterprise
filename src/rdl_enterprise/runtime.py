from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from .mb_graph import MBGraph, MBNode
from .h_state import HState
from .snapshot import (
    BusinessInput,
    InterpretationPrediction,
    FeedbackResult,
    CaseSnapshot,
    CaseStatus,
)
from .cascade import InterpCascade
from .human import HumanQuery

@dataclass
class TicketDispatchResult:
    """チケット受付・回答結果（事後結果受領前）"""
    ticket_id: str
    prediction: InterpretationPrediction
    hitl_required: bool
    hitl_reason: str
    action_taken: str
    final_output: str
    cost_tier: int
    status: CaseStatus = CaseStatus.PENDING
    has_candidate_knowledge: bool = False


@dataclass
class TicketResolutionResult:
    """事後結果受領・代謝反映結果"""
    ticket_id: str
    status: CaseStatus
    e_prediction: float
    e_input: float
    current_h: float
    current_theta_eff: float
    transition_to_m_delta: bool
    promoted_to_mb: bool = False


@dataclass
class TicketExecutionResult:
    """同期実行用の総合結果"""
    ticket_id: str
    prediction: InterpretationPrediction
    hitl_required: bool
    hitl_reason: str
    action_taken: str
    final_output: str
    status: CaseStatus
    e_prediction: Optional[float]
    e_input: Optional[float]
    current_h: float
    current_theta_eff: float
    transition_to_m_delta: bool
    cost_tier: int
    promoted_to_mb: bool = False


class EnterpriseRuntime:
    """
    RDL業務AI ランタイムコア
    非同期ライフサイクルと知識昇格ガバナンス（検証後沈澱）を司る
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

        # 非同期案件スナップショット管理
        self.pending_snapshots: Dict[str, CaseSnapshot] = {}
        self.resolved_snapshots: List[CaseSnapshot] = []

        # 運用メトリクス
        self.processed_tickets_count = 0
        self.auto_resolved_count = 0
        self.hitl_count = 0
        self.m_delta_count = 0
        self.cost_tier_counts = {0: 0, 1: 0, 2: 0, 3: 0}

    def dispatch_ticket(
        self,
        efp: BusinessInput,
        human_override_answer: Optional[str] = None,
        is_authoritative: bool = False,
    ) -> TicketDispatchResult:
        """
        フェーズ1：チケット受付・推論・アクション実行
        人間回答がある場合でも、正式権限(is_authoritative=True)でない限り
        結果検証前は M_B へ即時昇格させず、candidate_knowledge として保持する。
        """
        self.processed_tickets_count += 1

        # 1. 多層カスケード推論 (EFP -> F)
        pred = self.cascade.interpret(efp)
        self.cost_tier_counts[pred.cost_tier] = self.cost_tier_counts.get(pred.cost_tier, 0) + 1

        # 2. CaseSnapshot 作成（PENDING）
        snapshot = CaseSnapshot(
            efp=efp,
            f_pred=pred,
            candidate_knowledge=human_override_answer,
            is_authoritative=is_authoritative,
        )
        self.pending_snapshots[efp.ticket_id] = snapshot

        # 3. 人間問い合わせ (HITL) ゲート判定
        matched_node = self.mb_graph.get(pred.matched_node_id) if pred.matched_node_id else None
        hitl_eval = self.human.evaluate(efp, pred, matched_node)
        hitl_required = hitl_eval["must_ask"]
        hitl_reason = hitl_eval["reason"]

        if hitl_required:
            self.hitl_count += 1

        # 4. アクション実行・回答
        if hitl_required and human_override_answer:
            action_taken = "human_assisted"
            final_output = human_override_answer
            # 正式な権限者指示の場合は即時沈澱
            if is_authoritative:
                self.cascade.crystallize_rule(efp, human_override_answer, efp.category or "general", approved=True)
        else:
            action_taken = pred.action_type
            final_output = pred.content

        return TicketDispatchResult(
            ticket_id=efp.ticket_id,
            prediction=pred,
            hitl_required=hitl_required,
            hitl_reason=hitl_reason,
            action_taken=action_taken,
            final_output=final_output,
            cost_tier=pred.cost_tier,
            status=CaseStatus.PENDING,
            has_candidate_knowledge=bool(human_override_answer and not is_authoritative),
        )

    def resolve_ticket_feedback(
        self,
        ticket_id: str,
        feedback: FeedbackResult,
    ) -> TicketResolutionResult:
        """
        フェーズ2：後続結果 EFP' の回収と代謝反映
        ・結果が成功（SUCCESS）した場合にのみ、保持していた candidate_knowledge を M_B へ正式昇格。
        ・結果が失敗（FAILURE/REJECTED）した場合は、人間の助言であっても M_B 昇格を阻止して汚染を防ぐ。
        """
        if ticket_id not in self.pending_snapshots:
            raise KeyError(f"Ticket ID '{ticket_id}' は保留中(PENDING)に存在しません。")

        snapshot = self.pending_snapshots.pop(ticket_id)
        e_pred, e_input = snapshot.record_feedback(feedback)
        self.resolved_snapshots.append(snapshot)

        pred = snapshot.f_pred
        matched_node = self.mb_graph.get(pred.matched_node_id) if pred.matched_node_id else None

        promoted_to_mb = False
        # 学習ガバナンス：成功確認後にのみ M_B へ昇格（沈澱）
        if snapshot.status == CaseStatus.SUCCESS and snapshot.candidate_knowledge and not snapshot.is_authoritative:
            self.cascade.crystallize_rule(
                snapshot.efp,
                snapshot.candidate_knowledge,
                snapshot.efp.category or "general",
                approved=True,
            )
            promoted_to_mb = True

        # 熱 H の蓄積
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

        # 閾値判定 (H >= θ_eff)
        should_leap, hot_node, current_h = self.h_state.should_leap(pred.matched_node_id)
        current_theta = self.h_state.theta_eff()

        transition_m_delta = False

        if should_leap:
            self.m_delta_count += 1
            transition_m_delta = True
            self._execute_m_delta_reorganization(hot_node, snapshot.efp, feedback)
        else:
            # 通常運転：局所更新 (dM_B/dt)
            if matched_node:
                if feedback.user_resolved and not feedback.human_rejected:
                    matched_node.record_success(approved=feedback.human_approved)
                else:
                    matched_node.record_failure(rejected=feedback.human_rejected)

            if snapshot.status == CaseStatus.SUCCESS and not snapshot.efp_prime.human_approved:
                # 人間の直接代行なしで解決できた場合
                self.auto_resolved_count += 1

        return TicketResolutionResult(
            ticket_id=ticket_id,
            status=snapshot.status,
            e_prediction=e_pred,
            e_input=e_input,
            current_h=current_h,
            current_theta_eff=current_theta,
            transition_to_m_delta=transition_m_delta,
            promoted_to_mb=promoted_to_mb,
        )

    def expire_pending_tickets(self, ticket_ids: Optional[List[str]] = None) -> List[TicketResolutionResult]:
        """
        PENDING 案件のタイムアウト処理
        回収不能案件を UNKNOWN へ送り、軽微な熱と未回収指標 ξ_obs を更新する。
        """
        target_ids = ticket_ids if ticket_ids is not None else list(self.pending_snapshots.keys())
        results = []

        for tid in target_ids:
            if tid not in self.pending_snapshots:
                continue
            snapshot = self.pending_snapshots.pop(tid)
            e_pred, e_input = snapshot.mark_unknown()
            self.resolved_snapshots.append(snapshot)

            target_nid = snapshot.f_pred.matched_node_id or "__unmatched__"
            self.h_state.add_heat(target_nid, pred_err=e_pred, input_err=e_input)
            self.h_state.record_observation(unclassified=True, missing_info=True)

            results.append(TicketResolutionResult(
                ticket_id=tid,
                status=CaseStatus.UNKNOWN,
                e_prediction=e_pred,
                e_input=e_input,
                current_h=self.h_state.global_heat.total(),
                current_theta_eff=self.h_state.theta_eff(),
                transition_to_m_delta=False,
                promoted_to_mb=False,
            ))

        # 散逸
        inertias = {nid: n.inertia() for nid, n in self.mb_graph.nodes.items()}
        self.h_state.dissipate(inertias)
        return results

    def handle_ticket(
        self,
        efp: BusinessInput,
        feedback: Optional[FeedbackResult] = None,
        human_override_answer: Optional[str] = None,
        is_authoritative: bool = False,
    ) -> TicketExecutionResult:
        """
        同期／即時実行用ヘルパーメソッド
        """
        dispatch_res = self.dispatch_ticket(
            efp,
            human_override_answer=human_override_answer,
            is_authoritative=is_authoritative,
        )

        if feedback is not None:
            resol_res = self.resolve_ticket_feedback(efp.ticket_id, feedback)
            return TicketExecutionResult(
                ticket_id=efp.ticket_id,
                prediction=dispatch_res.prediction,
                hitl_required=dispatch_res.hitl_required,
                hitl_reason=dispatch_res.hitl_reason,
                action_taken=dispatch_res.action_taken,
                final_output=dispatch_res.final_output,
                status=resol_res.status,
                e_prediction=resol_res.e_prediction,
                e_input=resol_res.e_input,
                current_h=resol_res.current_h,
                current_theta_eff=resol_res.current_theta_eff,
                transition_to_m_delta=resol_res.transition_to_m_delta,
                cost_tier=dispatch_res.cost_tier,
                promoted_to_mb=resol_res.promoted_to_mb,
            )
        else:
            current_h = self.h_state.global_heat.total()
            current_theta = self.h_state.theta_eff()
            return TicketExecutionResult(
                ticket_id=efp.ticket_id,
                prediction=dispatch_res.prediction,
                hitl_required=dispatch_res.hitl_required,
                hitl_reason=dispatch_res.hitl_reason,
                action_taken=dispatch_res.action_taken,
                final_output=dispatch_res.final_output,
                status=CaseStatus.PENDING,
                e_prediction=None,
                e_input=None,
                current_h=current_h,
                current_theta_eff=current_theta,
                transition_to_m_delta=False,
                cost_tier=dispatch_res.cost_tier,
                promoted_to_mb=False,
            )

    def _execute_m_delta_reorganization(self, hot_node_id: str, efp: BusinessInput, feedback: FeedbackResult):
        node = self.mb_graph.get(hot_node_id)
        if node:
            if feedback.new_knowledge_provided:
                node.action_template["payload"] = feedback.new_knowledge_provided
            node.confidence = 0.6
            node.failure_count = 0
            node.rejection_count = 0
            node.success_count = 1

        self.h_state.apply_remaining_heat_after_leap(hot_node_id, remaining_ratio=0.2)

    def get_metrics(self) -> Dict[str, Any]:
        total = max(1, self.processed_tickets_count)
        resolved_count = len(self.resolved_snapshots)
        unknown_count = sum(1 for s in self.resolved_snapshots if s.status == CaseStatus.UNKNOWN)

        return {
            "total_tickets_received": self.processed_tickets_count,
            "pending_tickets_count": len(self.pending_snapshots),
            "resolved_tickets_count": resolved_count,
            "unknown_tickets_count": unknown_count,
            # 受信母数ベースの自動解決率
            "auto_resolution_rate_received": self.auto_resolved_count / total,
            # 回収済み母数ベースの正確な自動解決率
            "auto_resolution_rate_resolved": (self.auto_resolved_count / resolved_count) if resolved_count > 0 else 0.0,
            "hitl_rate": self.hitl_count / total,
            "m_delta_transitions": self.m_delta_count,
            "cost_tier_distribution": {k: v / total for k, v in self.cost_tier_counts.items()},
            "current_theta_eff": self.h_state.theta_eff(),
            "average_kappa": self.mb_graph.average_kappa(),
            "total_inertia": self.mb_graph.total_inertia(),
        }
