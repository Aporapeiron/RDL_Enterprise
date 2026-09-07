from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from datetime import datetime
import copy

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
from .authority import AuthorityContext
from .durability import DurabilityHarness
from .shadow import ShadowEvaluator, ShadowReport

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
    reorganization_proposal_id: Optional[str] = None


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
    reorganization_proposal_id: Optional[str] = None


@dataclass
class ReorganizationProposal:
    """再編相 M_Δ で起草された候補 M_B' とその耐久検査結果"""
    proposal_id: str
    hot_node_id: str
    candidate_mb: MBGraph
    durability_test_result: Dict[str, Any]
    status: str = "awaiting_approval"  # "awaiting_approval" | "promoted" | "rejected"
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    promoted_at: Optional[str] = None
    approved_by: Optional[str] = None


class EnterpriseRuntime:
    """
    RDL業務AI ランタイムコア
    非同期ライフサイクル、学習ガバナンス、および再編相 M_Δ（候補M_B'起草・破断検査・承認昇格）を司る
    """
    def __init__(
        self,
        mb_graph: Optional[MBGraph] = None,
        theta_0: float = 2.0,
        gamma: float = 0.05,
        llm_bridge: Optional[Any] = None,
        durability_harness: Optional[DurabilityHarness] = None,
        auto_promote_reorganizations: bool = False,  # 破断検査合格時の自動昇格フラグ (デフォルトは厳格にFalse)
        auto_promote_authority: Optional[AuthorityContext] = None,  # 事前委任された権限コンテキスト (限定スコープ用)
    ):
        self.mb_graph = mb_graph or MBGraph()
        self.h_state = HState(theta_0=theta_0, gamma=gamma)
        self.cascade = InterpCascade(self.mb_graph, llm_bridge=llm_bridge)
        self.human = HumanQuery()
        self.durability_harness = durability_harness or DurabilityHarness()
        self.auto_promote_reorganizations = auto_promote_reorganizations
        self.auto_promote_authority = auto_promote_authority

        # 非同期案件スナップショット管理
        self.pending_snapshots: Dict[str, CaseSnapshot] = {}
        self.resolved_snapshots: List[CaseSnapshot] = []

        # 再編相 M_Δ プロポーザル管理
        self.pending_reorganizations: Dict[str, ReorganizationProposal] = {}
        self.reorganization_history: List[ReorganizationProposal] = []

        # シャドウ並行推論エンジン (本番 M_B vs 候補 M_B')
        self.active_shadow_evaluator: Optional[ShadowEvaluator] = None

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
        authority: Optional[AuthorityContext] = None,
    ) -> TicketDispatchResult:
        """
        フェーズ1：チケット受付・推論・アクション実行
        AuthorityContext が権限を持つ正式指示でない限り、助言は candidate_knowledge として保留する。
        """
        self.processed_tickets_count += 1

        # 1. 多層カスケード推論 (EFP -> F)
        pred = self.cascade.interpret(efp)
        self.cost_tier_counts[pred.cost_tier] = self.cost_tier_counts.get(pred.cost_tier, 0) + 1

        # シャドウ並行推論 (有効な場合、候補 M_B' でも並行推論して差分を記録)
        if self.active_shadow_evaluator:
            self.active_shadow_evaluator.evaluate_input(efp)

        is_authoritative = False
        if authority and authority.is_authorized_for(efp.category or "general"):
            is_authoritative = True

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
            # 正式な権限者指示の場合のみ即時沈澱
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
        成功確認後に candidate_knowledge を昇格。
        H >= θ_eff 時は、再編相 M_Δ で候補 M_B' の起草と耐久検査パイプラインを起動する。
        """
        if ticket_id not in self.pending_snapshots:
            raise KeyError(f"Ticket ID '{ticket_id}' は保留中(PENDING)に存在しません。")

        snapshot = self.pending_snapshots.pop(ticket_id)
        e_pred, e_input = snapshot.record_feedback(feedback)
        self.resolved_snapshots.append(snapshot)

        # シャドウ三者比較の記録 (有効な場合)
        if self.active_shadow_evaluator:
            self.active_shadow_evaluator.record_feedback(ticket_id, feedback)

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
        proposal_id = None

        if should_leap:
            self.m_delta_count += 1
            transition_m_delta = True
            # 再編相 M_Δ パイプライン発動！
            proposal = self._trigger_m_delta_proposal(hot_node, snapshot.efp, feedback)
            proposal_id = proposal.proposal_id

            # 自動昇格設定かつ全テスト合格の場合（委任された正式権限コンテキストが存在する場合のみ実行）
            if (
                self.auto_promote_reorganizations
                and self.auto_promote_authority is not None
                and proposal.durability_test_result.get("all_passed")
            ):
                self.promote_candidate_mb(
                    proposal_id,
                    authority=self.auto_promote_authority,
                )
        else:
            # 通常運転：局所更新 (dM_B/dt)
            if matched_node:
                if feedback.user_resolved and not feedback.human_rejected:
                    matched_node.record_success(approved=feedback.human_approved)
                else:
                    matched_node.record_failure(rejected=feedback.human_rejected)

            if snapshot.status == CaseStatus.SUCCESS and not snapshot.efp_prime.human_approved:
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
            reorganization_proposal_id=proposal_id,
        )

    def _trigger_m_delta_proposal(
        self,
        hot_node_id: str,
        efp: BusinessInput,
        feedback: FeedbackResult,
    ) -> ReorganizationProposal:
        """
        高負荷再編相 M_Δ パイプライン:
        1. 既存 M_B をディープコピーして候補 M_B' を起草
        2. 新知識を反映して不整合ノードを再構築
        3. DurabilityHarness による破断検査（履歴・境界）を実行
        4. プロポーザルとして登録
        """
        # 1. 候補 M_B' の起草（ディープコピー）
        candidate_dict = self.mb_graph.to_dict()
        candidate_mb = MBGraph.from_dict(candidate_dict)

        # 2. 該当ノードの再編
        node = candidate_mb.get(hot_node_id)
        if node:
            if feedback.new_knowledge_provided:
                node.action_template["payload"] = feedback.new_knowledge_provided
            node.confidence = 0.6
            node.failure_count = 0
            node.rejection_count = 0
            node.success_count = 1

        # 3. 破断検査（Durability Test）の実行
        test_result = self.durability_harness.run_all(candidate_mb, self.resolved_snapshots)

        proposal_id = f"prop_{len(self.reorganization_history) + len(self.pending_reorganizations) + 1:03d}"
        proposal = ReorganizationProposal(
            proposal_id=proposal_id,
            hot_node_id=hot_node_id,
            candidate_mb=candidate_mb,
            durability_test_result=test_result,
            status="awaiting_approval",
        )
        self.pending_reorganizations[proposal_id] = proposal
        return proposal

    def promote_candidate_mb(self, proposal_id: str, authority: AuthorityContext) -> bool:
        """
        権限者による正式承認を経て、候補 M_B' を本番へスワップ（Leap完了）
        再編後は H_remaining を引き継ぎ冷却する。
        """
        if proposal_id not in self.pending_reorganizations:
            return False

        proposal = self.pending_reorganizations.pop(proposal_id)
        hot_node = self.mb_graph.get(proposal.hot_node_id)
        target_domain = hot_node.domain if hot_node else "all"

        # 権限チェック
        if not authority.is_authorized_for(target_domain):
            proposal.status = "rejected"
            self.reorganization_history.append(proposal)
            return False

        # 本番 M_B の置換（Leap）
        self.mb_graph = proposal.candidate_mb
        self.cascade.mb_graph = self.mb_graph
        # キャッシュのクリア（新構造へ適応）
        self.cascade.level0_cache.clear()

        # 残存熱 H_remaining の算出・引き継ぎ (冷却)
        self.h_state.apply_remaining_heat_after_leap(proposal.hot_node_id, remaining_ratio=0.2)

        proposal.status = "promoted"
        proposal.promoted_at = datetime.utcnow().isoformat()
        proposal.approved_by = f"{authority.role}:{authority.actor_id}"
        self.reorganization_history.append(proposal)

        # 昇格したプロポーザルがシャドウ実行中だった場合、シャドウを終了
        if self.active_shadow_evaluator and self.active_shadow_evaluator.proposal_id == proposal_id:
            self.active_shadow_evaluator = None

        return True

    def enable_shadow_mode(self, proposal_id: str, max_allowed_regression_rate: float = 0.05) -> bool:
        """
        再編候補 M_B' をシャドウ推論エンジンにセットし、本番並行評価を開始する
        """
        if proposal_id not in self.pending_reorganizations:
            return False
        proposal = self.pending_reorganizations[proposal_id]
        self.active_shadow_evaluator = ShadowEvaluator(
            proposal_id=proposal_id,
            prod_mb=self.mb_graph,
            candidate_mb=proposal.candidate_mb,
            max_allowed_regression_rate=max_allowed_regression_rate,
        )
        return True

    def disable_shadow_mode(self) -> Optional[ShadowReport]:
        """シャドウ並行評価を停止し、最終レポートを返す"""
        if not self.active_shadow_evaluator:
            return None
        report = self.active_shadow_evaluator.generate_report()
        self.active_shadow_evaluator = None
        return report

    def get_shadow_report(self) -> Optional[ShadowReport]:
        """現在のシャドウ評価レポートを取得"""
        if not self.active_shadow_evaluator:
            return None
        return self.active_shadow_evaluator.generate_report()

    def expire_pending_tickets(self, ticket_ids: Optional[List[str]] = None) -> List[TicketResolutionResult]:
        """PENDING 案件のタイムアウト処理"""
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

        inertias = {nid: n.inertia() for nid, n in self.mb_graph.nodes.items()}
        self.h_state.dissipate(inertias)
        return results

    def handle_ticket(
        self,
        efp: BusinessInput,
        feedback: Optional[FeedbackResult] = None,
        human_override_answer: Optional[str] = None,
        authority: Optional[AuthorityContext] = None,
    ) -> TicketExecutionResult:
        """同期／即時実行用ヘルパーメソッド"""
        dispatch_res = self.dispatch_ticket(
            efp,
            human_override_answer=human_override_answer,
            authority=authority,
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
                reorganization_proposal_id=resol_res.reorganization_proposal_id,
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

    def get_metrics(self) -> Dict[str, Any]:
        total = max(1, self.processed_tickets_count)
        resolved_count = len(self.resolved_snapshots)
        unknown_count = sum(1 for s in self.resolved_snapshots if s.status == CaseStatus.UNKNOWN)

        return {
            "total_tickets_received": self.processed_tickets_count,
            "pending_tickets_count": len(self.pending_snapshots),
            "resolved_tickets_count": resolved_count,
            "unknown_tickets_count": unknown_count,
            "auto_resolution_rate_received": self.auto_resolved_count / total,
            "auto_resolution_rate_resolved": (self.auto_resolved_count / resolved_count) if resolved_count > 0 else 0.0,
            "hitl_rate": self.hitl_count / total,
            "m_delta_transitions": self.m_delta_count,
            "reorganizations_promoted": sum(1 for r in self.reorganization_history if r.status == "promoted"),
            "cost_tier_distribution": {k: v / total for k, v in self.cost_tier_counts.items()},
            "current_theta_eff": self.h_state.theta_eff(),
            "average_kappa": self.mb_graph.average_kappa(),
            "total_inertia": self.mb_graph.total_inertia(),
        }
