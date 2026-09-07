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
from .canary import CanaryManager, CanaryDeployment, CanaryStatus, CanaryCompletionPolicy
from .promotion_gate import ProposalState, PromotionPolicy, PromotionGate

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
    is_canary: bool = False


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
    canary_rolled_back: bool = False
    canary_rollback_reason: Optional[str] = None


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
    """再編相 M_Δ で起草された候補 M_B' とその耐久検査結果・昇格状態機械"""
    proposal_id: str
    hot_node_id: str
    candidate_mb: MBGraph
    durability_test_result: Dict[str, Any]
    policy: PromotionPolicy = field(default_factory=PromotionPolicy)
    shadow_report: Optional[ShadowReport] = None
    status: ProposalState = ProposalState.DRAFT
    reasons: List[str] = field(default_factory=list)
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
        default_promotion_policy: Optional[PromotionPolicy] = None,  # カスタム昇格ポリシー (未指定時はドメイン標準)
    ):
        self.mb_graph = mb_graph or MBGraph()
        self.h_state = HState(theta_0=theta_0, gamma=gamma)
        self.cascade = InterpCascade(self.mb_graph, llm_bridge=llm_bridge)
        self.human = HumanQuery()
        self.durability_harness = durability_harness or DurabilityHarness()
        self.auto_promote_reorganizations = auto_promote_reorganizations
        self.auto_promote_authority = auto_promote_authority
        self.default_promotion_policy = default_promotion_policy

        # 非同期案件スナップショット管理
        self.pending_snapshots: Dict[str, CaseSnapshot] = {}
        self.resolved_snapshots: List[CaseSnapshot] = []

        # 再編相 M_Δ プロポーザル管理
        self.pending_reorganizations: Dict[str, ReorganizationProposal] = {}
        self.reorganization_history: List[ReorganizationProposal] = []

        # シャドウ並行推論エンジン (本番 M_B vs 候補 M_B')
        self.active_shadow_evaluator: Optional[ShadowEvaluator] = None

        # カナリア展開・監視マネージャー (Leap後の段階的配分と自動ロールバック)
        self.canary_manager = CanaryManager()

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

        # カナリアルーティング判定 (有効な場合、新 M_B' へ振り分け)
        is_canary = False
        active_cascade = self.cascade
        active_graph = self.mb_graph
        if self.canary_manager.should_route_to_canary(efp):
            is_canary = True
            active_graph = self.canary_manager.active_deployment.canary_mb
            active_cascade = InterpCascade(active_graph, llm_bridge=self.cascade.llm_bridge)

        # 1. 多層カスケード推論 (EFP -> F)
        pred = active_cascade.interpret(efp)
        self.cost_tier_counts[pred.cost_tier] = self.cost_tier_counts.get(pred.cost_tier, 0) + 1

        # シャドウ並行推論 (有効な場合、本番実績予測 pred を渡し、候補 M_B' でも並行推論して差分を記録)
        if self.active_shadow_evaluator:
            self.active_shadow_evaluator.evaluate_input(efp, prod_pred=pred)

        is_authoritative = False
        if authority and authority.is_authorized_for(efp.category or "general"):
            is_authoritative = True

        # 2. CaseSnapshot 作成（PENDING）
        snapshot = CaseSnapshot(
            efp=efp,
            f_pred=pred,
            candidate_knowledge=human_override_answer,
            is_authoritative=is_authoritative,
            is_canary=is_canary,
        )
        self.pending_snapshots[efp.ticket_id] = snapshot

        # 3. 人間問い合わせ (HITL) ゲート判定
        matched_node = active_graph.get(pred.matched_node_id) if pred.matched_node_id else None
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

        # 5. 外界作用台帳 (ActionLedger) への記録 (Model Rollback / World Rollback 追跡)
        mb_ver = getattr(active_graph, "version", "prod")
        compensating_action = None
        if is_canary:
            compensating_action = {
                "type": "send_correction_or_revert",
                "original_output": final_output,
                "revert_notice": f"【システム訂正】案件 {efp.ticket_id} の回答を取り消し・訂正いたします。",
            }

        self.canary_manager.action_ledger.record_action(
            ticket_id=efp.ticket_id,
            mb_version=mb_ver,
            is_canary=is_canary,
            action_type=action_taken,
            payload=final_output,
            is_reversible=True,
            compensating_action=compensating_action,
        )

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
            is_canary=is_canary,
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

        # 局所学習およびノード参照対象の厳格分離 (Canary新M_B' vs 旧本番M_B)
        if snapshot.is_canary and self.canary_manager.active_deployment:
            target_graph = self.canary_manager.active_deployment.canary_mb
            target_cascade = InterpCascade(target_graph, llm_bridge=self.cascade.llm_bridge)
        else:
            target_graph = self.mb_graph
            target_cascade = self.cascade

        matched_node = target_graph.get(pred.matched_node_id) if pred.matched_node_id else None

        promoted_to_mb = False
        # 学習ガバナンス：成功確認後にのみ M_B へ昇格（沈澱）
        if snapshot.status == CaseStatus.SUCCESS and snapshot.candidate_knowledge and not snapshot.is_authoritative:
            target_cascade.crystallize_rule(
                snapshot.efp,
                snapshot.candidate_knowledge,
                snapshot.efp.category or "general",
                approved=True,
            )
            promoted_to_mb = True

        # 熱 H の蓄積 (Version-aware: カナリアの熱は本番熱状態を汚染させない)
        target_nid = pred.matched_node_id or "__unmatched__"
        mb_ver = getattr(target_graph, "version", "prod")
        self.h_state.add_heat(
            target_nid,
            pred_err=e_pred,
            input_err=e_input,
            mb_version=mb_ver,
            is_canary=snapshot.is_canary,
        )
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

            # 自動昇格設定かつ委任権限が存在する場合（PromotionGate で検証）
            if (
                self.auto_promote_reorganizations
                and self.auto_promote_authority is not None
            ):
                self.promote_candidate_mb(
                    proposal_id,
                    authority=self.auto_promote_authority,
                    is_automated=True,
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

        # カナリア監視と自動ロールバック判定
        canary_rolled_back = False
        canary_rollback_reason = None
        if snapshot.is_canary and self.canary_manager.active_deployment:
            is_rb, rb_reason = self.canary_manager.record_feedback(
                ticket_id=ticket_id,
                is_canary=True,
                e_pred=e_pred,
                e_input=e_input,
                rejected=feedback.human_rejected,
            )
            if is_rb:
                canary_rolled_back = True
                canary_rollback_reason = rb_reason
                # 旧本番の復元とキャッシュクリア
                last_dep = self.canary_manager.deployment_history[-1]
                self.mb_graph = last_dep.prod_mb_backup
                self.cascade.mb_graph = self.mb_graph
                self.cascade.level0_cache.clear()
                # 該当プロポーザルを REGRESSED 状態へ
                if last_dep.proposal_id in self.pending_reorganizations:
                    prop = self.pending_reorganizations.pop(last_dep.proposal_id)
                    prop.status = ProposalState.REGRESSED
                    prop.reasons.append(f"カナリア自動ロールバック: {rb_reason}")
                    self.reorganization_history.append(prop)

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
            canary_rolled_back=canary_rolled_back,
            canary_rollback_reason=canary_rollback_reason,
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

        # 4. リスクベースの昇格ポリシー決定と初期ゲート判定
        target_domain = node.domain if node else "general"
        policy = self.default_promotion_policy or PromotionPolicy.default_for_domain(target_domain)

        gate_res = PromotionGate.evaluate_readiness(
            current_state=ProposalState.DRAFT,
            durability_result=test_result,
            shadow_report=None,
            policy=policy,
        )

        proposal_id = f"prop_{len(self.reorganization_history) + len(self.pending_reorganizations) + 1:03d}"
        proposal = ReorganizationProposal(
            proposal_id=proposal_id,
            hot_node_id=hot_node_id,
            candidate_mb=candidate_mb,
            durability_test_result=test_result,
            policy=policy,
            status=gate_res.next_state,
            reasons=gate_res.reasons,
        )
        self.pending_reorganizations[proposal_id] = proposal
        return proposal

    def promote_candidate_mb(
        self,
        proposal_id: str,
        authority: AuthorityContext,
        is_automated: bool = False,
        use_canary: bool = False,
        canary_ratio: float = 0.1,
        theta_canary: float = 1.5,
        max_canary_failures: int = 1,
    ) -> bool:
        """
        PromotionGate（準備性検証）と権限者（AuthorityContext）の二重ゲートを通過した場合のみ、
        候補 M_B' を本番へスワップ（Leap完了）する。
        再編後は H_remaining を引き継ぎ冷却する。
        """
        if proposal_id not in self.pending_reorganizations:
            return False

        proposal = self.pending_reorganizations[proposal_id]
        hot_node = self.mb_graph.get(proposal.hot_node_id)
        target_domain = hot_node.domain if hot_node else "all"

        # 最新のシャドウレポートを反映
        if self.active_shadow_evaluator and self.active_shadow_evaluator.proposal_id == proposal_id:
            proposal.shadow_report = self.active_shadow_evaluator.generate_report()

        # ゲート1：昇格準備性 (Readiness) の検証 (Durability / Shadow / Evidence)
        gate_res = PromotionGate.evaluate_readiness(
            current_state=proposal.status,
            durability_result=proposal.durability_test_result,
            shadow_report=proposal.shadow_report,
            policy=proposal.policy,
        )
        proposal.status = gate_res.next_state
        proposal.reasons = gate_res.reasons

        if not gate_res.can_promote:
            # 準備未達のため昇格拒絶
            return False

        # ゲート2：権限者 (Authority) および自動昇格ポリシーの検証
        if not PromotionGate.verify_authority_for_promotion(
            authority=authority,
            target_domain=target_domain,
            policy=proposal.policy,
            is_automated=is_automated,
        ):
            if not authority.is_authorized_for(target_domain):
                fail_reason = f"ドメイン管轄権限の不適合 (actor={authority.actor_id}, scope={authority.scope}, target={target_domain})"
            elif proposal.policy.require_human_approval and not authority.is_human_authenticated():
                fail_reason = f"人間承認要件の不適合: 認証されたHuman主体ではありません (actor={authority.actor_id}, type={authority.actor_type}, auth_by={authority.authenticated_by})"
            elif is_automated and proposal.policy.require_human_approval:
                fail_reason = f"自動昇格制限に抵触: 人間承認が必須のポリシーです (actor={authority.actor_id})"
            else:
                fail_reason = f"権限不適合または人間承認要件に抵触: actor={authority.actor_id}, role={authority.role}"

            proposal.status = ProposalState.REJECTED
            proposal.reasons.append(fail_reason)
            self.pending_reorganizations.pop(proposal_id)
            self.reorganization_history.append(proposal)
            return False

        # 二重ゲート通過！
        if use_canary:
            # カナリア展開を開始し、旧本番を維持したままトラフィックの一部を新 M_B' へ振り分ける
            self.canary_manager.start_canary(
                proposal_id=proposal_id,
                current_prod_mb=self.mb_graph,
                candidate_mb=proposal.candidate_mb,
                target_domain=target_domain,
                initial_ratio=canary_ratio,
                theta_canary=theta_canary,
                max_allowed_failures=max_canary_failures,
            )
            # シャドウを終了
            if self.active_shadow_evaluator and self.active_shadow_evaluator.proposal_id == proposal_id:
                self.active_shadow_evaluator = None
            return True

        # 一括置換 (Leap)
        self.pending_reorganizations.pop(proposal_id)
        self.mb_graph = proposal.candidate_mb
        self.cascade.mb_graph = self.mb_graph
        self.cascade.level0_cache.clear()

        # 残存熱 H_remaining の算出・引き継ぎ (冷却)
        self.h_state.apply_remaining_heat_after_leap(proposal.hot_node_id, remaining_ratio=0.2)

        proposal.status = ProposalState.PROMOTED
        proposal.promoted_at = datetime.utcnow().isoformat()
        proposal.approved_by = f"{authority.role}:{authority.actor_id}"
        self.reorganization_history.append(proposal)

        # 昇格したプロポーザルがシャドウ実行中だった場合、シャドウを終了
        if self.active_shadow_evaluator and self.active_shadow_evaluator.proposal_id == proposal_id:
            self.active_shadow_evaluator = None

        return True

    def step_up_canary(self, new_ratio: float) -> bool:
        """カナリア配分比率を拡大 (例: 0.1 -> 0.5 -> 1.0)"""
        return self.canary_manager.step_up_traffic(new_ratio)

    def complete_canary_rollout(self, policy: Optional[CanaryCompletionPolicy] = None) -> bool:
        """カナリア展開を完了し、新 M_B' を本番として確定コミット (エビデンス検証を含む)"""
        if not self.canary_manager.active_deployment:
            return False

        prop_id = self.canary_manager.active_deployment.proposal_id
        new_mb = self.canary_manager.complete_rollout(policy=policy)
        if not new_mb:
            return False

        # 本番 M_B の完全置換
        self.mb_graph = new_mb
        self.cascade.mb_graph = self.mb_graph
        self.cascade.level0_cache.clear()

        if prop_id in self.pending_reorganizations:
            proposal = self.pending_reorganizations.pop(prop_id)
            proposal.status = ProposalState.PROMOTED
            proposal.promoted_at = datetime.utcnow().isoformat()
            self.reorganization_history.append(proposal)
            self.h_state.apply_remaining_heat_after_leap(proposal.hot_node_id, remaining_ratio=0.2)

        return True

    def rollback_active_canary(self, reason: str = "手動指示によるロールバック") -> bool:
        """アクティブなカナリア展開を中断し、旧本番 M_B へ即時復元"""
        if not self.canary_manager.active_deployment:
            return False

        prop_id = self.canary_manager.active_deployment.proposal_id
        restored_mb = self.canary_manager.trigger_rollback(reason)
        if not restored_mb:
            return False

        # 旧本番の復元
        self.mb_graph = restored_mb
        self.cascade.mb_graph = self.mb_graph
        self.cascade.level0_cache.clear()

        if prop_id in self.pending_reorganizations:
            proposal = self.pending_reorganizations.pop(prop_id)
            proposal.status = ProposalState.REGRESSED
            proposal.reasons.append(f"カナリアロールバック: {reason}")
            self.reorganization_history.append(proposal)

        return True

    def enable_shadow_mode(
        self,
        proposal_id: str,
        max_allowed_regression_rate: float = 0.05,
        minimum_resolved_cases: int = 1,
    ) -> bool:
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
            minimum_resolved_cases=minimum_resolved_cases,
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
