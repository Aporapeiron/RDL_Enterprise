from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from datetime import datetime, timezone
import copy

from .mb_graph import MBGraph, MBNode
from .h_state import HState
from .snapshot import (
    BusinessInput,
    InterpretationPrediction,
    FeedbackResult,
    CaseSnapshot,
    CaseStatus,
    FrozenInterpretationContext,
    LLMBridgeIdentity,
    ObservedOutcome,
)
from .persistence import SQLiteCaseStore
from .cascade import InterpCascade, CascadeConfig
from .human import HumanQuery
from .authority import AuthorityContext
from .durability import DurabilityHarness
from .shadow import ShadowEvaluator, ShadowReport
from .canary import CanaryManager, CanaryDeployment, CanaryStatus, CanaryCompletionPolicy, ActionCapability
from .promotion_gate import ProposalState, PromotionPolicy, PromotionGate
from .constraint import (
    ConstraintConfig, ConstraintContext, RelationConstraintLocator, RuptureProbe,
    compute_efp_prime_constraint, compute_opposing_conflict_strength,
)

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
        external_compensation_client: Optional[Any] = None,  # 外部補償API/メッセージングクライアント (fail-closed防止)
        store_path: Optional[str] = None,
    ):
        self.mb_graph = mb_graph or MBGraph()
        self.h_state = HState(theta_0=theta_0, gamma=gamma)
        self.cascade = InterpCascade(self.mb_graph, llm_bridge=llm_bridge)
        self.human = HumanQuery()
        self.durability_harness = durability_harness or DurabilityHarness()
        self.auto_promote_reorganizations = auto_promote_reorganizations
        self.auto_promote_authority = auto_promote_authority
        self.default_promotion_policy = default_promotion_policy
        self.external_compensation_client = external_compensation_client
        self.case_store = SQLiteCaseStore(store_path) if store_path else None
        if self.case_store:
            persisted = self.case_store.load_runtime_state()
            if persisted:
                self.mb_graph = MBGraph.from_dict(persisted["mb_graph"])
                self.h_state = persisted["h_state"]
                self.cascade.mb_graph = self.mb_graph
                self.cascade.import_cache(persisted.get("level0_cache", {}))

        # 非同期案件スナップショット管理
        self.pending_snapshots: Dict[str, CaseSnapshot] = {}
        self.resolved_snapshots: List[CaseSnapshot] = []
        if self.case_store:
            for ticket_id, snapshot in self.case_store.load_pending():
                self.pending_snapshots[ticket_id] = snapshot
            self.resolved_snapshots.extend(self.case_store.load_resolved())

        # 再編相 M_Δ プロポーザル管理
        self.pending_reorganizations: Dict[str, ReorganizationProposal] = {}
        self.reorganization_history: List[ReorganizationProposal] = []
        if self.case_store and persisted:
            self.pending_reorganizations.update(persisted.get("pending_reorganizations", {}))
            self.reorganization_history.extend(persisted.get("reorganization_history", []))

        # シャドウ並行推論エンジン (本番 M_B vs 候補 M_B')
        self.active_shadow_evaluator: Optional[ShadowEvaluator] = None

        # カナリア展開・監視マネージャー (Leap後の段階的配分と自動ロールバック)
        self.canary_manager = CanaryManager()
        if self.case_store and persisted:
            self.canary_manager.active_deployment = persisted.get("active_canary_deployment")
            self.canary_manager.deployment_history = list(persisted.get("canary_deployment_history", []))
            self.canary_manager.action_ledger.records = list(persisted.get("action_ledger_records", []))

        # 外界作用ロールバック用の社内標準訂正ハンドラを登録 (外部接続または fail-closed)
        self.canary_manager.action_ledger.executor.register_handler(
            "send_correction_or_revert",
            self._handle_correction_or_revert,
        )

        # 運用メトリクス
        self.processed_tickets_count = 0
        self.auto_resolved_count = 0
        self.hitl_count = 0
        self.m_delta_count = 0
        self.timeout_count = 0
        self.cost_tier_counts = {0: 0, 1: 0, 2: 0, 3: 0}

    def _handle_correction_or_revert(self, action_record: Any) -> Dict[str, Any]:
        """
        社内チャット・メール等に対する訂正通知発行の実ハンドラ。
        公理B5 (Zero Trust): 外部クライアント接続時は実送信を実行し、未接続時は fail-closed (success=False) とする。
        """
        if self.external_compensation_client is not None:
            if hasattr(self.external_compensation_client, "send_revert"):
                return self.external_compensation_client.send_revert(action_record)
            elif callable(self.external_compensation_client):
                return self.external_compensation_client(action_record)

        # 外部補償クライアント未接続時は成功と偽装せず fail-closed で遮断
        return {
            "success": False,
            "action_id": action_record.action_id,
            "ticket_id": action_record.ticket_id,
            "reason": "外部補償クライアント未接続 (fail-closed: 外界取り消し未確認)",
            "timestamp": datetime.utcnow().isoformat(),
        }

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

        # 1. T0 SPEC 4, 6.1 / BASE v2.0 §4.2:
        # 更新前 M_B グラフおよび解釈環境全体（関係拘束評価設定・評価時刻を含む）を先行完全凍結
        # F (事前予測) と F' (事後解釈) は全く同一の凍結コンテキスト・同一キャッシュ・
        # 同一の関係拘束評価条件（ConstraintConfig + 評価時刻）から解釈される
        frozen_graph = MBGraph.from_dict(active_graph.to_dict())
        frozen_graph.freeze()
        cache_snapshot = active_cascade.export_cache()
        llm_id = LLMBridgeIdentity.from_bridge(self.cascade.llm_bridge)
        cascade_cfg = getattr(active_cascade, "config", CascadeConfig())

        # cascade の constraint_locator.config を FrozenContext へ伝播
        constraint_cfg = getattr(
            getattr(active_cascade, "constraint_locator", None), "config", None
        )

        frozen_ctx = FrozenInterpretationContext(
            mb_version=getattr(active_graph, "version", "v1.0"),
            mb_content_hash=getattr(active_graph, "content_hash", lambda: "unknown")(),
            frozen_mb=frozen_graph,
            target_domain=efp.category,
            llm_bridge=self.cascade.llm_bridge,
            initial_level0_cache=cache_snapshot,
            cascade_config=copy.deepcopy(cascade_cfg),
            llm_identity=llm_id,
            constraint_config=copy.deepcopy(constraint_cfg) if constraint_cfg is not None else None,
            # constraint_evaluation_time は __post_init__ が dispatch 時刻で自動凍結する
        )

        # 2. 同一凍結コンテキスト（初期状態 C0）による事前多層推論 (EFP -> F)
        pred = frozen_ctx.interpret_efp(efp)
        self.cost_tier_counts[pred.cost_tier] = self.cost_tier_counts.get(pred.cost_tier, 0) + 1

        # シャドウ並行推論 (有効な場合、本番実績予測 pred を渡し、候補 M_B' でも並行推論して差分を記録)
        if self.active_shadow_evaluator:
            self.active_shadow_evaluator.evaluate_input(efp, prod_pred=pred)

        is_authoritative = False
        if authority and authority.is_authorized_for(efp.category or "general"):
            is_authoritative = True

        # 3. 対象ノード取得と CaseSnapshot 作成（PENDING: 更新前 M_B 前提を完全凍結保存）
        matched_node = active_graph.get(pred.matched_node_id) if pred.matched_node_id else None
        frozen_node = copy.deepcopy(matched_node) if matched_node else None

        trace = pred.metadata.get("interpretation_trace")
        snapshot = CaseSnapshot(
            efp=efp,
            f_pred=pred,
            candidate_knowledge=human_override_answer,
            is_authoritative=is_authoritative,
            is_canary=is_canary,
            frozen_node_snapshot=frozen_node,
            frozen_context=frozen_ctx,
            interpretation_trace=trace,
        )
        self.pending_snapshots[efp.ticket_id] = snapshot
        if self.case_store:
            self.case_store.save_case(efp.ticket_id, snapshot, snapshot.status.value)
            self.case_store.record_event("ticket_dispatched", efp.ticket_id, {"status": snapshot.status.value})
            self._persist_runtime_state()

        # 3. 人間問い合わせ (HITL) ゲート判定
        hitl_eval = self.human.evaluate(efp, pred, matched_node)
        hitl_required = hitl_eval["must_ask"]
        hitl_reason = hitl_eval["reason"]

        if hitl_required:
            self.hitl_count += 1

        # 4. アクション実行・回答
        if hitl_required and human_override_answer:
            action_taken = "human_assisted"
            final_output = human_override_answer
            # 正式な権限者指示の場合のみ方針ルール策定 (Authoritative Policy Injection: BASE v2.0 §4.2)
            # ※ ただし Canary 案件の場合は候補 M_B' の Freeze 原則および旧本番保護のため直接の策定を遮断
            if is_authoritative and not is_canary and authority is not None:
                self.cascade.inject_authoritative_rule(
                    efp=efp,
                    policy_text=human_override_answer,
                    category=efp.category or "general",
                    authority=authority,
                )
        else:
            action_taken = pred.action_type
            final_output = pred.content

        # 5. 外界作用台帳 (ActionLedger) への記録 (Model Rollback / World Rollback 追跡)
        mb_ver = getattr(active_graph, "version", "prod")
        compensating_action = None
        dep_id = None
        prop_id = None

        # 作用定義 (MBNode / action_template) から capability を貫通取得
        node_cap = None
        if matched_node and hasattr(matched_node, "action_template") and isinstance(matched_node.action_template, dict):
            raw_cap = matched_node.action_template.get("capability")
            if raw_cap:
                try:
                    node_cap = ActionCapability(raw_cap)
                except ValueError:
                    node_cap = None

        if is_canary and self.canary_manager.active_deployment:
            dep_id = self.canary_manager.active_deployment.deployment_id
            prop_id = self.canary_manager.active_deployment.proposal_id
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
            deployment_id=dep_id,
            proposal_id=prop_id,
            capability=node_cap,
            compensating_action=compensating_action,
        )
        if self.case_store:
            self._persist_runtime_state()

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
        at: Optional[Any] = None,
        operation_id: Optional[str] = None,
        actor_provenance: Optional[Dict[str, Any]] = None,
    ) -> TicketResolutionResult:
        """
        フェーズ2：後続結果 EFP' の回収と代謝反映
        成功確認後に candidate_knowledge を昇格。
        H >= θ_eff 時は、再編相 M_Δ で候補 M_B' の起草と耐久検査パイプラインを起動する。
        """
        if self.case_store and operation_id:
            prior = self.case_store.get_operation(operation_id)
            if prior is not None:
                operation_type, prior_ticket_id, result = prior
                if operation_type != "ticket_resolved" or prior_ticket_id != ticket_id:
                    raise ValueError(f"operation_id '{operation_id}' is already bound to another operation")
                return result

        if ticket_id not in self.pending_snapshots:
            raise KeyError(f"Ticket ID '{ticket_id}' は保留中(PENDING)に存在しません。")

        snapshot = self.pending_snapshots.pop(ticket_id)
        e_pred, e_input = snapshot.record_feedback(feedback, at=at)
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

        # T0 代謝規律：
        # 1. 拘束検査・破断判定・H 蓄積は、更新前の完全凍結グラフ (frozen_mb) を対象として行う！
        # 2. その後、成功確認後にのみ新ルールの沈澱 (crystallize_rule) や live グラフの実績更新を行う。
        frozen_ctx = getattr(snapshot, "frozen_context", None)
        eval_graph = getattr(frozen_ctx, "frozen_mb", None) if frozen_ctx else target_graph
        eval_matched_node = eval_graph.get(pred.matched_node_id) if pred.matched_node_id else None
        # live 更新用ノード (target_graph 側)
        matched_node = target_graph.get(pred.matched_node_id) if pred.matched_node_id else None

        target_nid = pred.matched_node_id or "__unmatched__"
        mb_ver = getattr(eval_graph, "version", getattr(target_graph, "version", "prod"))

        # -------------------------------------------------------------
        # RuptureProbe & 対向拘束強度 (C_old × C_prime) の算出 (BASE v2.0 §4.2)
        # 【時刻概念の厳密な分離】:
        #   1. M_B 側の解釈条件時刻 mb_eval_time (t):
        #      dispatch 時に凍結された constraint_evaluation_time。
        #      更新前 M_B のノード群の freshness や破断判定を、F と F' で同一条件に保つ。
        #   2. EFP' 側の事後入力評価時刻 feedback_eval_time (t+Δ):
        #      フィードバック受領時点の時刻。後続入力 EFP' (provenance.observed_at) の
        #      新鮮さ・時点拘束 C_prime を、事後観測時点から評価する。
        # -------------------------------------------------------------
        c_old = 0.5
        rupture_opposing = 1.0
        frozen_constraint_cfg = (
            getattr(frozen_ctx, "constraint_config", None) if frozen_ctx else None
        )
        frozen_eval_time = (
            getattr(frozen_ctx, "constraint_evaluation_time", None) if frozen_ctx else None
        )
        mb_eval_time = frozen_eval_time or datetime.now(timezone.utc)
        constraint_cfg = frozen_constraint_cfg or ConstraintConfig()

        # EFP' 側の事後観測時刻 t+Δ
        feedback_eval_time = datetime.now(timezone.utc)
        if snapshot.resolved_at:
            try:
                parsed_time = datetime.fromisoformat(snapshot.resolved_at)
                if parsed_time.tzinfo is None:
                    parsed_time = parsed_time.replace(tzinfo=timezone.utc)
                feedback_eval_time = parsed_time
            except Exception:
                pass

        target_locus_ids = list(pred.constraint_locus_ids) if pred.constraint_locus_ids else ([pred.matched_node_id] if pred.matched_node_id else [])
        if target_locus_ids:
            try:
                actual_token = getattr(snapshot, "actual_replay_token", None)
                if actual_token is None and snapshot.f_pred is not None:
                    actual_token = getattr(snapshot.f_pred, "replay_token", None)

                ctx = ConstraintContext(
                    efp=snapshot.efp,
                    current_time=mb_eval_time,
                    mb_version=mb_ver,
                    active_domain=snapshot.efp.category,
                    config=constraint_cfg,  # 凍結された設定を使用
                    frozen_context=frozen_ctx,  # 完全同一の凍結推論器を伝播
                    llm_bridge=self.cascade.llm_bridge,
                    actual_replay_token=actual_token,
                )
                locator = RelationConstraintLocator(constraint_cfg)
                # 責任拘束位置群から拘束束 C_old を取得 (凍結時刻 t で評価)
                bundle = locator.locate_bundle_for_locus(eval_graph, target_locus_ids, ctx)
                if bundle is not None:
                    c_old = bundle.constraint_score
                    probe = RuptureProbe(constraint_cfg)
                    # 凍結グラフ eval_graph 上で破断検査を実行
                    rupture = probe.probe(bundle, eval_graph, ctx)
                    if rupture.verdict == "break":
                        rupture_opposing = rupture.opposing_strength
            except Exception:
                pass

        # 後続 EFP' 側の拘束 C_prime を抽出（事後入力評価時刻 feedback_eval_time を反映）
        c_prime = compute_efp_prime_constraint(feedback, snapshot, current_time=feedback_eval_time)
        has_conflict = bool(e_pred > 0 or e_input > 0 or feedback.human_rejected or rupture_opposing > 1.0)
        # C_old (既存拘束) と C_prime (後続拘束) の衝突から実効対向拘束強度を算出
        opposing_strength = max(rupture_opposing, compute_opposing_conflict_strength(c_old, c_prime, has_conflict))

        # 共通代謝終端処理 (Metabolic Terminal)
        return self._finalize_case_metabolism(
            snapshot=snapshot,
            status=snapshot.status,
            e_pred=e_pred,
            e_input=e_input,
            feedback=feedback,
            opposing_strength=opposing_strength,
            is_timeout=False,
            at=at or getattr(snapshot, "resolved_at", None),
            operation_id=operation_id,
            actor_provenance=actor_provenance,
        )

    def _persist_runtime_state(self) -> None:
        if self.case_store:
            self.case_store.save_runtime_state({
                "mb_graph": self.mb_graph.to_dict(),
                "h_state": self.h_state,
                "level0_cache": self.cascade.export_cache(),
                "pending_reorganizations": self.pending_reorganizations,
                "reorganization_history": self.reorganization_history,
                "active_canary_deployment": self.canary_manager.active_deployment,
                "canary_deployment_history": self.canary_manager.deployment_history,
                "action_ledger_records": self.canary_manager.action_ledger.records,
            })

    def _finalize_case_metabolism(
        self,
        snapshot: CaseSnapshot,
        status: CaseStatus,
        e_pred: float,
        e_input: float,
        feedback: Optional[FeedbackResult] = None,
        opposing_strength: float = 1.0,
        is_timeout: bool = False,
        at: Optional[Any] = None,
        operation_id: Optional[str] = None,
        actor_provenance: Optional[Dict[str, Any]] = None,
    ) -> TicketResolutionResult:
        """
        全案件（通常フィードバック解決／タイムアウト）に共通する代謝終端処理 (BASE v2.0 代謝閉ループ)
        - 熱 H の Version-aware 蓄積
        - Canary への熱隔離・自動ロールバック判定
        - 成功案件の Level 0 キャッシュおよび M_B への沈澱
        - 自然散逸・θ_eff 判定・再編相 M_Δ パイプライン発動
        - 局所更新 (dM_B/dt)
        """
        ticket_id = snapshot.efp.ticket_id
        pred = snapshot.f_pred
        target_nid = pred.matched_node_id or "__unmatched__"

        # グラフバージョンと隔離スコープの特定
        if snapshot.is_canary and self.canary_manager.active_deployment:
            target_graph = self.canary_manager.active_deployment.canary_mb
            target_cascade = InterpCascade(target_graph, llm_bridge=self.cascade.llm_bridge)
            mb_ver = getattr(target_graph, "version", "canary")
        else:
            target_graph = self.mb_graph
            target_cascade = self.cascade
            frozen_ctx = getattr(snapshot, "frozen_context", None)
            eval_graph = getattr(frozen_ctx, "frozen_mb", None) if frozen_ctx else self.mb_graph
            mb_ver = getattr(eval_graph, "version", getattr(self.mb_graph, "version", "prod"))

        # 熱 H の蓄積 (Version-aware: カナリアの熱は本番熱状態を汚染させない)
        self.h_state.add_heat(
            target_nid,
            pred_err=e_pred,
            input_err=e_input,
            mb_version=mb_ver,
            is_canary=snapshot.is_canary,
            opposing_constraint_strength=opposing_strength,
        )
        rejected = feedback.human_rejected if feedback else False
        self.h_state.record_observation(
            unclassified=(pred.matched_node_id is None),
            missing_info=(e_input > 0),
            unknown_input=(pred.cost_tier == 3),
            rejected=rejected,
            mb_version=mb_ver,
            is_canary=snapshot.is_canary,
        )

        # 学習ガバナンス：拘束検査および H 蓄積の完了後、成功確認案件のみ M_B へ昇格・沈澱
        # ※ Canary 期間中は候補 M_B' の Freeze 原則 (Identity Drift 防止) のため直接結晶化はスキップ
        promoted_to_mb = False
        evidence_at = at or getattr(snapshot, "resolved_at", None)
        if not is_timeout and not snapshot.is_canary and status == CaseStatus.SUCCESS and feedback and feedback.user_resolved and not feedback.human_rejected:
            if snapshot.candidate_knowledge and not snapshot.is_authoritative:
                target_cascade.crystallize_rule(
                    snapshot.efp,
                    snapshot.candidate_knowledge,
                    snapshot.efp.category or "general",
                    approved=True,
                )
                promoted_to_mb = True
            elif pred.matched_node_id:
                # 既知ルールやLLM解決で特定された既存ノードによる判断を Level 0 キャッシュへ沈澱 (最小代謝閉ループ: Tier 1/3 -> Tier 0)
                target_cascade.sediment_level0(
                    domain=snapshot.efp.category or "general",
                    query_text=snapshot.efp.query_text,
                    node_id=pred.matched_node_id,
                )

        # 閾値判定および局所更新の分岐：
        canary_rolled_back = False
        canary_rollback_reason = None
        transition_m_delta = False
        proposal_id = None

        if snapshot.is_canary:
            # カナリア案件：本番散逸・本番M_Δ判定を完全遮断
            current_canary_h = self.h_state.version_total_heat(mb_ver)
            current_theta = self.canary_manager.active_deployment.theta_canary if self.canary_manager.active_deployment else 1.5
            current_h = current_canary_h

            if self.canary_manager.active_deployment:
                is_rb, rb_reason = self.canary_manager.record_feedback(
                    ticket_id=ticket_id,
                    is_canary=True,
                    e_pred=e_pred,
                    e_input=e_input,
                    rejected=rejected,
                    current_heat=current_canary_h,
                )
                if is_rb:
                    canary_rolled_back = True
                    canary_rollback_reason = rb_reason
                    last_dep = self.canary_manager.deployment_history[-1]
                    self.mb_graph = last_dep.prod_mb_backup
                    self.cascade.mb_graph = self.mb_graph
                    self.cascade.level0_cache.clear()
                    if last_dep.proposal_id in self.pending_reorganizations:
                        prop = self.pending_reorganizations.pop(last_dep.proposal_id)
                        prop.status = ProposalState.REGRESSED
                        prop.reasons.append(f"カナリア自動ロールバック: {rb_reason}")
                        self.reorganization_history.append(prop)
        else:
            # 本番案件：自然散逸および本番M_Δ起草判定
            node_inertias = {nid: n.inertia() for nid, n in target_graph.nodes.items()}
            self.h_state.dissipate(node_inertias)

            current_prod_h = self.h_state.version_total_heat(mb_ver)
            current_theta = self.h_state.theta_eff(mb_ver)
            current_h = current_prod_h

            if current_prod_h >= current_theta:
                transition_m_delta = True
                hot_id = self.h_state.hottest_node_for_version(mb_ver) or pred.matched_node_id or "__global__"
                proposal = self._trigger_m_delta_proposal(hot_id, snapshot.efp, feedback, at=evidence_at)
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
                        at=evidence_at,
                    )
            else:
                # 通常運転：局所更新 (dM_B/dt)
                matched_node = target_graph.get(pred.matched_node_id) if pred.matched_node_id else None
                if matched_node:
                    if not is_timeout and status == CaseStatus.SUCCESS and feedback and feedback.user_resolved and not feedback.human_rejected:
                        matched_node.record_success(approved=feedback.human_approved, at=evidence_at)
                    elif not is_timeout and status in (CaseStatus.FAILURE, CaseStatus.REJECTED):
                        matched_node.record_failure(rejected=rejected, at=evidence_at)
                    elif is_timeout or status == CaseStatus.UNKNOWN:
                        # 観測不能・タイムアウト (UNKNOWN): 判断の誤りではないため failure_count / confidence は変更せず未解決として記録
                        if hasattr(matched_node, "record_unresolved"):
                            matched_node.record_unresolved(at=evidence_at)

                if status == CaseStatus.SUCCESS and (not feedback or not getattr(snapshot.efp_prime, "human_approved", False)):
                    self.auto_resolved_count += 1

        result = TicketResolutionResult(
            ticket_id=ticket_id,
            status=status,
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
        if self.case_store:
            # Persist only after H/cache/M_B metabolism has completed.
            state = {
                "mb_graph": self.mb_graph.to_dict(), "h_state": self.h_state,
                "level0_cache": self.cascade.export_cache(),
                "pending_reorganizations": self.pending_reorganizations,
                "reorganization_history": self.reorganization_history,
                "active_canary_deployment": self.canary_manager.active_deployment,
                "canary_deployment_history": self.canary_manager.deployment_history,
                "action_ledger_records": self.canary_manager.action_ledger.records,
            }
            self.case_store.commit_transition(ticket_id, snapshot, state, {
                "status": snapshot.status.value,
                "actor": actor_provenance,
            }, operation_id, result)
        return result

    def _trigger_m_delta_proposal(
        self,
        hot_node_id: str,
        efp: BusinessInput,
        feedback: Optional[FeedbackResult] = None,
        at: Optional[str] = None,
    ) -> ReorganizationProposal:
        """
        高負荷再編相 M_Δ パイプライン:
        1. 既存 M_B をディープコピーして候補 M_B' を起草
        2. 新知識を反映して不整合ノードを再構築
        3. DurabilityHarness による破断検査（履歴・境界）を実行
        4. プロポーザルとして登録
        """
        # 1. 候補 M_B' の起草（ディープコピーと一意な候補バージョンの自動採番）
        candidate_dict = self.mb_graph.to_dict()
        candidate_mb = MBGraph.from_dict(candidate_dict)

        proposal_id = f"prop_{len(self.reorganization_history) + len(self.pending_reorganizations) + 1:03d}"
        parent_ver = getattr(self.mb_graph, "version", "v1.0")
        candidate_mb.version = f"{parent_ver}-cand-{proposal_id}"

        # 2. 該当ノードの再編
        node = candidate_mb.get(hot_node_id)
        if node:
            if feedback and feedback.new_knowledge_provided:
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
            candidate_mb=candidate_mb,
        )

        proposal_created_at = (
            at
            or (feedback.observed_at if feedback and getattr(feedback, "observed_at", None) else None)
            or getattr(efp, "created_at", None)
            or datetime.utcnow().isoformat()
        )

        proposal = ReorganizationProposal(
            proposal_id=proposal_id,
            hot_node_id=hot_node_id,
            candidate_mb=candidate_mb,
            durability_test_result=test_result,
            policy=policy,
            status=gate_res.next_state,
            reasons=gate_res.reasons,
            created_at=proposal_created_at,
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
        at: Optional[str] = None,
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

        # ゲート1：昇格準備性 (Readiness) の検証 (Durability / Shadow / Evidence / Hash Binding)
        gate_res = PromotionGate.evaluate_readiness(
            current_state=proposal.status,
            durability_result=proposal.durability_test_result,
            shadow_report=proposal.shadow_report,
            policy=proposal.policy,
            candidate_mb=proposal.candidate_mb,
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
            self._persist_runtime_state()
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
            self._persist_runtime_state()
            return True

        # 一括置換 (Leap)
        self.pending_reorganizations.pop(proposal_id)
        self.mb_graph = proposal.candidate_mb
        self.cascade.mb_graph = self.mb_graph
        self.cascade.level0_cache.clear()

        # 残存熱 H_remaining の算出・引き継ぎ (冷却)
        self.h_state.apply_remaining_heat_after_leap(proposal.hot_node_id, remaining_ratio=0.2)

        proposal.status = ProposalState.PROMOTED
        proposal.promoted_at = at or (authority.timestamp if hasattr(authority, "timestamp") and authority.timestamp else None) or datetime.utcnow().isoformat()
        proposal.approved_by = f"{authority.role}:{authority.actor_id}"
        self.reorganization_history.append(proposal)

        # 昇格したプロポーザルがシャドウ実行中だった場合、シャドウを終了
        if self.active_shadow_evaluator and self.active_shadow_evaluator.proposal_id == proposal_id:
            self.active_shadow_evaluator = None

        self._persist_runtime_state()
        return True

    def step_up_canary(self, new_ratio: float) -> bool:
        """カナリア配分比率を拡大 (例: 0.1 -> 0.5 -> 1.0)"""
        changed = self.canary_manager.step_up_traffic(new_ratio)
        if changed:
            self._persist_runtime_state()
        return changed

    def complete_canary_rollout(self, policy: Optional[CanaryCompletionPolicy] = None, at: Optional[str] = None) -> bool:
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
            proposal.promoted_at = at or datetime.utcnow().isoformat()
            self.reorganization_history.append(proposal)
            self.h_state.apply_remaining_heat_after_leap(proposal.hot_node_id, remaining_ratio=0.2)
            # カナリア期間中の残存熱・観測統計を新本番へ合成・引き継ぎ (公理B4: 代謝の連続性)
            cand_ver = getattr(new_mb, "version", "unknown")
            self.h_state.inherit_canary_state_to_prod(canary_version=cand_ver, heat_ratio=0.5)

        self._persist_runtime_state()
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

        self._persist_runtime_state()
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

    def expire_pending_tickets(self, ticket_ids: Optional[List[str]] = None, at: Optional[Any] = None) -> List[TicketResolutionResult]:
        """
        PENDING 案件のタイムアウト処理 (共通代謝終端化: T0 BASE v2.0 §4.2)
        通常フィードバックと同様にバージョン境界・カナリア隔離を通し、
        カナリア案件のタイムアウト熱が本番 HState を汚染するのを物理的に遮断する。
        """
        target_ids = ticket_ids if ticket_ids is not None else list(self.pending_snapshots.keys())
        results = []

        for tid in target_ids:
            if tid not in self.pending_snapshots:
                continue
            snapshot = self.pending_snapshots.pop(tid)
            e_pred, e_input = snapshot.mark_unknown(at=at)
            self.resolved_snapshots.append(snapshot)
            self.timeout_count += 1

            res = self._finalize_case_metabolism(
                snapshot=snapshot,
                status=CaseStatus.UNKNOWN,
                e_pred=e_pred,
                e_input=e_input,
                feedback=None,
                opposing_strength=1.0,
                is_timeout=True,
                at=at or getattr(snapshot, "resolved_at", None),
            )
            results.append(res)

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

    def compute_state_digest(self) -> "RuntimeStateDigest":
        """
        AIコアの遷移関連状態ダイジェスト (T0 BASE v2.0 整合)
        M_B 単体だけでなく、保持熱、キャッシュ、保留中案件、起草中プロポーザル、
        シャドウ評価、カナリア展開、外界作用台帳まで含む、現在の観測境界で
        後続遷移に影響すると扱う状態の暗号論的要約。
        """
        import hashlib
        import json

        # 1. M_B グラフハッシュ
        mb_hash = self.mb_graph.content_hash() if hasattr(self.mb_graph, "content_hash") else "none"

        # 2. HState ハッシュ (全バージョン別熱量、グローバル熱、観測統計)
        v_heats = {}
        for (ver, nid), hv in sorted(self.h_state.versioned_heats.items()):
            v_heats[f"{ver}:{nid}"] = round(hv.total(self.h_state.w_pred, self.h_state.w_input), 4)
        h_payload = {
            "versioned_heats": v_heats,
            "global_heat": round(self.h_state.global_heat.total(self.h_state.w_pred, self.h_state.w_input), 4),
            "unclassified": self.h_state.unclassified_count,
            "missing_info": self.h_state.missing_info_count,
            "unknown_input": self.h_state.unknown_input_count,
            "rejections": self.h_state.rejection_events_count,
        }
        h_state_hash = hashlib.sha256(json.dumps(h_payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]

        # 3. Level 0 キャッシュハッシュ
        cache_items = sorted([f"{':'.join(str(x) for x in k)}->{v}" for k, v in self.cascade.level0_cache.items()])
        cache_hash = hashlib.sha256(json.dumps(cache_items).encode("utf-8")).hexdigest()[:16]

        # 4. 保留中スナップショットハッシュ (pending_snapshots)
        pending_list = []
        for tid in sorted(self.pending_snapshots.keys()):
            snap = self.pending_snapshots[tid]
            pending_list.append({
                "ticket_id": tid,
                "status": snap.status.value,
                "domain": snap.efp.category or "",
                "created_at": snap.efp.created_at,
            })
        pending_cases_hash = hashlib.sha256(json.dumps(pending_list, sort_keys=True).encode("utf-8")).hexdigest()[:16]

        # 5. 再編相プロポーザルハッシュ (pending_reorganizations)
        m_delta_list = []
        for pid in sorted(self.pending_reorganizations.keys()):
            prop = self.pending_reorganizations[pid]
            m_delta_list.append({
                "proposal_id": prop.proposal_id,
                "hot_node_id": prop.hot_node_id,
                "status": prop.status.value,
                "candidate_hash": prop.candidate_mb.content_hash() if hasattr(prop.candidate_mb, "content_hash") else "",
            })
        m_delta_hash = hashlib.sha256(json.dumps(m_delta_list, sort_keys=True).encode("utf-8")).hexdigest()[:16]

        # 6. シャドウ評価器ハッシュ
        shadow_info = {"active": False}
        if self.active_shadow_evaluator:
            shadow_info = {
                "active": True,
                "proposal_id": self.active_shadow_evaluator.proposal_id,
                "pending_pairs_count": len(self.active_shadow_evaluator.pending_pairs),
                "resolved_triplets_count": len(self.active_shadow_evaluator.resolved_triplets),
            }
        shadow_hash = hashlib.sha256(json.dumps(shadow_info, sort_keys=True).encode("utf-8")).hexdigest()[:16]

        # 7. カナリア展開ハッシュ
        canary_info = {"active": False}
        if self.canary_manager.active_deployment:
            ad = self.canary_manager.active_deployment
            canary_info = {
                "active": True,
                "deployment_id": ad.deployment_id,
                "proposal_id": ad.proposal_id,
                "status": ad.status.value,
                "traffic_ratio": ad.traffic_ratio,
                "cases_count": ad.canary_cases_count,
                "failure_count": ad.canary_failure_count,
            }
        canary_hash = hashlib.sha256(json.dumps(canary_info, sort_keys=True).encode("utf-8")).hexdigest()[:16]

        # 8. 外界作用台帳ハッシュ (action_ledger)
        ledger_records = []
        for rec in self.canary_manager.action_ledger.records:
            ledger_records.append({
                "action_id": rec.action_id,
                "ticket_id": rec.ticket_id,
                "status": rec.status,
                "capability": rec.capability.value,
            })
        action_ledger_hash = hashlib.sha256(json.dumps(ledger_records, sort_keys=True).encode("utf-8")).hexdigest()[:16]

        # 9. 解決済み履歴ハッシュ (resolved_snapshots: M_Δ起草時の耐久破断検査に影響する未来拘束状態)
        resolved_list = []
        for snap in self.resolved_snapshots:
            resolved_list.append({
                "ticket_id": snap.efp.ticket_id,
                "status": snap.status.value,
                "domain": snap.efp.category or "",
                "query": snap.efp.query_text,
                "resolved_at": getattr(snap, "resolved_at", ""),
                "e_pred": snap.e_prediction,
                "e_input": snap.e_input,
            })
        resolved_history_hash = hashlib.sha256(json.dumps(resolved_list, sort_keys=True).encode("utf-8")).hexdigest()[:16]

        # 10. 再編履歴ハッシュ (reorganization_history: 次期プロポーザル採番および昇格状態機械に影響)
        reorg_list = []
        for prop in self.reorganization_history:
            reorg_list.append({
                "proposal_id": prop.proposal_id,
                "hot_node_id": prop.hot_node_id,
                "status": prop.status.value,
                "created_at": prop.created_at,
                "promoted_at": prop.promoted_at,
            })
        reorg_history_hash = hashlib.sha256(json.dumps(reorg_list, sort_keys=True).encode("utf-8")).hexdigest()[:16]

        # 11. バージョン別観測統計プール (versioned_observations: ξ_obs / θ_eff の算出に影響)
        v_obs_dict = {}
        for ver, pool in sorted(self.h_state.versioned_observations.items()):
            v_obs_dict[ver] = dict(pool)
        observation_pool_hash = hashlib.sha256(json.dumps(v_obs_dict, sort_keys=True).encode("utf-8")).hexdigest()[:16]

        # 総合要約ハッシュ (全11要素の結合)
        full_payload = {
            "mb_hash": mb_hash,
            "h_state_hash": h_state_hash,
            "cache_hash": cache_hash,
            "pending_cases_hash": pending_cases_hash,
            "m_delta_hash": m_delta_hash,
            "shadow_hash": shadow_hash,
            "canary_hash": canary_hash,
            "action_ledger_hash": action_ledger_hash,
            "resolved_history_hash": resolved_history_hash,
            "reorg_history_hash": reorg_history_hash,
            "observation_pool_hash": observation_pool_hash,
        }
        digest_hash = hashlib.sha256(json.dumps(full_payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]

        return RuntimeStateDigest(
            digest_hash=digest_hash,
            mb_hash=mb_hash,
            h_state_hash=h_state_hash,
            cache_hash=cache_hash,
            pending_cases_hash=pending_cases_hash,
            m_delta_hash=m_delta_hash,
            shadow_hash=shadow_hash,
            canary_hash=canary_hash,
            action_ledger_hash=action_ledger_hash,
            resolved_history_hash=resolved_history_hash,
            reorg_history_hash=reorg_history_hash,
            observation_pool_hash=observation_pool_hash,
        )


@dataclass(frozen=True)
class RuntimeStateDigest:
    """
    RDL AI コアの遷移関連状態ダイジェスト (T0 BASE v2.0 §4.2 整合)
    認知・代謝・学習・保留・再編・試験・過去解決履歴・再編履歴・観測統計プールの
    後続挙動を拘束する有限サブシステム状態の条件固定再現用要約。
    """
    digest_hash: str
    mb_hash: str
    h_state_hash: str
    cache_hash: str
    pending_cases_hash: str
    m_delta_hash: str
    shadow_hash: str
    canary_hash: str
    action_ledger_hash: str
    resolved_history_hash: str
    reorg_history_hash: str
    observation_pool_hash: str
