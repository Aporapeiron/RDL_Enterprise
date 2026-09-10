import unittest
import sys
import os
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
from rdl_enterprise.snapshot import BusinessInput, FeedbackResult, CaseStatus
from rdl_enterprise.authority import AuthorityContext
from rdl_enterprise.promotion_gate import ProposalState, PromotionPolicy
from rdl_enterprise.runtime import EnterpriseRuntime, ReorganizationProposal
from rdl_enterprise.service import EnterpriseService, AuthenticationError, AuthorizationError
from rdl_enterprise.tool_execution import ToolSpec, ToolRegistry, execute_tool
from rdl_enterprise.canary import ActionCapability


class TestProductAcceptanceMetabolicLoop(unittest.TestCase):
    """
    RDL Enterprise 製品受入テスト: 最小代謝閉ループ実証
    「経験する -> 成功確認 -> 判断が M_B に沈澱 -> 次回推論が Tier 0 へ軽量化」
    """

    def setUp(self):
        self.prod_graph = MBGraph(version="v1.0")
        self.prod_graph.commit_node(MBNode(
            id="node_wf_ringi",
            domain="workflow",
            trigger_pattern={"exact_keys": ["稟議申請の方法"]},
            action_template={"type": "direct_reply", "payload": "社内ワークフローポータルから申請してください"},
            confidence=0.8,
        ), origin=CommitmentOrigin.TEST_FIXTURE)

        self.candidate_graph = MBGraph(version="v2.0-cand")
        self.candidate_graph.commit_node(MBNode(
            id="node_wf_ringi",
            domain="workflow",
            trigger_pattern={"exact_keys": ["稟議申請の方法"]},
            action_template={"type": "direct_reply", "payload": "新SaaSワークフローポータルから申請してください"},
            confidence=0.9,
        ), origin=CommitmentOrigin.TEST_FIXTURE)

    def test_pending_case_recovers_after_runtime_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store_path = os.path.join(directory, "enterprise.sqlite3")
            first = EnterpriseRuntime(mb_graph=self.prod_graph, store_path=store_path)
            ticket = BusinessInput(
                ticket_id="T_RESTART_01",
                user_id="U_RESTART",
                category="workflow",
                query_text="稟議申請の方法",
            )

            first.dispatch_ticket(ticket)
            self.assertIn(ticket.ticket_id, first.pending_snapshots)

            restarted = EnterpriseRuntime(mb_graph=self.prod_graph, store_path=store_path)
            self.assertIn(ticket.ticket_id, restarted.pending_snapshots)

            result = restarted.resolve_ticket_feedback(
                ticket.ticket_id,
                FeedbackResult(user_resolved=True, actual_response_text="解決しました"),
            )
            self.assertEqual(result.ticket_id, ticket.ticket_id)
            self.assertNotIn(ticket.ticket_id, restarted.pending_snapshots)

    def test_brain_state_persists_after_successful_metabolism(self):
        with tempfile.TemporaryDirectory() as directory:
            store_path = os.path.join(directory, "brain.sqlite3")
            first = EnterpriseRuntime(mb_graph=self.prod_graph, store_path=store_path)
            ticket = BusinessInput("T_BRAIN_01", "U_BRAIN", "workflow", "稟議申請の方法")
            first.dispatch_ticket(ticket)
            first.resolve_ticket_feedback(
                ticket.ticket_id,
                FeedbackResult(user_resolved=True, actual_response_text="解決しました"),
            )
            expected_heat = first.h_state.global_heat.total()
            expected_cache = first.cascade.export_cache()

            restarted = EnterpriseRuntime(mb_graph=MBGraph(), store_path=store_path)
            self.assertEqual(restarted.h_state.global_heat.total(), expected_heat)
            self.assertEqual(restarted.cascade.export_cache(), expected_cache)
            self.assertEqual(restarted.mb_graph.content_hash(), first.mb_graph.content_hash())
            self.assertEqual(len(restarted.resolved_snapshots), 1)
            self.assertEqual(restarted.resolved_snapshots[0].efp.ticket_id, ticket.ticket_id)

    def test_feedback_operation_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = EnterpriseRuntime(mb_graph=self.prod_graph, store_path=os.path.join(directory, "ops.sqlite3"))
            ticket = BusinessInput("T_OP_01", "U_OP", "workflow", "稟議申請の方法")
            runtime.dispatch_ticket(ticket)
            first = runtime.resolve_ticket_feedback(
                ticket.ticket_id, FeedbackResult(user_resolved=True), operation_id="op-feedback-01"
            )
            heat = runtime.h_state.global_heat.total()
            retry = runtime.resolve_ticket_feedback(
                ticket.ticket_id, FeedbackResult(user_resolved=True), operation_id="op-feedback-01"
            )
            self.assertEqual(retry, first)
            self.assertEqual(runtime.h_state.global_heat.total(), heat)

    def test_operation_id_collision_is_rejected_before_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = EnterpriseRuntime(mb_graph=self.prod_graph, store_path=os.path.join(directory, "ops.sqlite3"))
            runtime.dispatch_ticket(BusinessInput("T_OP_A", "U_OP", "workflow", "稟議申請の方法"))
            runtime.resolve_ticket_feedback("T_OP_A", FeedbackResult(user_resolved=True), operation_id="op-collision")
            runtime.dispatch_ticket(BusinessInput("T_OP_B", "U_OP", "workflow", "稟議申請の方法"))
            heat = runtime.h_state.global_heat.total()
            with self.assertRaises(ValueError):
                runtime.resolve_ticket_feedback("T_OP_B", FeedbackResult(user_resolved=True), operation_id="op-collision")
            self.assertIn("T_OP_B", runtime.pending_snapshots)
            self.assertEqual(runtime.h_state.global_heat.total(), heat)

    def test_authenticated_service_completes_business_ticket(self):
        service = EnterpriseService(EnterpriseRuntime(mb_graph=self.prod_graph))
        actor = AuthorityContext("operator-1", "operator", "workflow", "human", "idp_sso")
        ticket = BusinessInput("T_SERVICE_01", "U_SERVICE", "workflow", "稟議申請の方法")
        dispatched = service.submit(ticket, actor)
        resolved = service.record_feedback(ticket.ticket_id, FeedbackResult(user_resolved=True), actor, "service-op-1")
        self.assertEqual(dispatched.ticket_id, resolved.ticket_id)

    def test_service_rejects_unauthenticated_actor(self):
        service = EnterpriseService(EnterpriseRuntime(mb_graph=self.prod_graph))
        actor = AuthorityContext("operator-1", "operator", "workflow", "human", None)
        with self.assertRaises(AuthenticationError):
            service.submit(BusinessInput("T_SERVICE_02", "U_SERVICE", "workflow", "稟議申請の方法"), actor)

    def test_service_rejects_untrusted_auth_method_and_scope_mismatch(self):
        service = EnterpriseService(EnterpriseRuntime(mb_graph=self.prod_graph))
        request = BusinessInput("T_SERVICE_03", "U_SERVICE", "workflow", "稟議申請の方法")
        with self.assertRaises(AuthenticationError):
            service.submit(request, AuthorityContext("u1", "operator", "workflow", "human", "untrusted"))
        with self.assertRaises(AuthorizationError):
            service.submit(request, AuthorityContext("u2", "operator", "finance", "human", "idp_sso"))

    def test_idempotent_replay_still_checks_scope_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "service.sqlite3")
            runtime = EnterpriseRuntime(mb_graph=self.prod_graph, store_path=path)
            service = EnterpriseService(runtime)
            workflow = AuthorityContext("workflow-user", "operator", "workflow", "human", "idp_sso")
            finance = AuthorityContext("finance-user", "operator", "finance", "human", "idp_sso")
            ticket = BusinessInput("T_SERVICE_REPLAY", "U_SERVICE", "workflow", "稟議申請の方法")
            service.submit(ticket, workflow)
            service.record_feedback(ticket.ticket_id, FeedbackResult(user_resolved=True), workflow, "op-service-replay")
            restarted = EnterpriseService(EnterpriseRuntime(mb_graph=MBGraph(), store_path=path))
            with self.assertRaises(AuthorizationError):
                restarted.record_feedback(ticket.ticket_id, FeedbackResult(user_resolved=True), finance, "op-service-replay")

    def test_authorized_tool_execution_records_action(self):
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph)
        service = EnterpriseService(runtime)
        registry = ToolRegistry()
        registry.register(ToolSpec("workflow.lookup", "workflow", ActionCapability.DRY_RUN_ONLY, lambda p: {"found": p["ticket"]}))
        actor = AuthorityContext("tool-user", "operator", "workflow", "human", "idp_sso")
        result = execute_tool(service, registry, "workflow.lookup", {"ticket": "T-TOOL"}, actor, "T-TOOL")
        self.assertEqual(result.output, {"found": "T-TOOL"})
        self.assertEqual(runtime.canary_manager.action_ledger.records[-1].action_type, "tool:workflow.lookup")

    def test_tool_scope_is_checked_before_execution(self):
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph)
        service = EnterpriseService(runtime)
        registry = ToolRegistry()
        called = []
        registry.register(ToolSpec("finance.lookup", "finance", handler=lambda p: called.append(p)))
        actor = AuthorityContext("tool-user", "operator", "workflow", "human", "idp_sso")
        with self.assertRaises(AuthorizationError):
            execute_tool(service, registry, "finance.lookup", {}, actor, "T-TOOL-2")
        self.assertEqual(called, [])

    def test_metabolic_closed_loop_tier1_to_tier0(self):
        """
        受入条件 1: Tier 1 ルール適合 -> 成功確認 -> Tier 0 キャッシュ沈澱 -> 次回同一クエリが Tier 0 解決
        """
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph)

        # 1. 初回ディスパッチ (Level 1 確定ルール一致 -> Tier 1)
        efp1 = BusinessInput(ticket_id="T001", user_id="U1", category="workflow", query_text="稟議申請の方法")
        res1 = runtime.dispatch_ticket(efp1)
        self.assertEqual(res1.prediction.cost_tier, 1)
        self.assertEqual(res1.prediction.matched_node_id, "node_wf_ringi")
        self.assertEqual(res1.status, CaseStatus.PENDING)

        # この時点では live runtime の level0_cache にはまだ沈澱していない (未確認の判断は恒久化しない)
        norm_key = ("v1.0", "workflow", runtime.cascade._normalize("稟議申請の方法"))
        self.assertNotIn(norm_key, runtime.cascade.level0_cache)

        # 2. 成功フィードバックを受領
        feedback = FeedbackResult(user_resolved=True, human_approved=True)
        res_feedback = runtime.resolve_ticket_feedback("T001", feedback)
        self.assertEqual(res_feedback.status, CaseStatus.SUCCESS)

        # 成功確認により、live runtime の level0_cache に判断が正式沈澱したこと
        self.assertIn(norm_key, runtime.cascade.level0_cache)
        self.assertEqual(runtime.cascade.level0_cache[norm_key], "node_wf_ringi")

        # 3. 2回目の同一クエリディスパッチ -> Tier 0 (Level 0 キャッシュヒット、確信度ブースト)
        efp2 = BusinessInput(ticket_id="T002", user_id="U2", category="workflow", query_text="稟議申請の方法")
        res2 = runtime.dispatch_ticket(efp2)
        self.assertEqual(res2.prediction.cost_tier, 0)
        self.assertEqual(res2.prediction.matched_node_id, "node_wf_ringi")
        self.assertGreater(res2.prediction.confidence, res1.prediction.confidence)
        self.assertIn("社内ワークフローポータル", res2.final_output)

    def test_metabolic_closed_loop_tier3_crystallize_to_tier0(self):
        """
        受入条件 2: 未知質問 (Tier 3) -> 先輩/LLMの助言解決 -> 成功確認 -> M_B結晶化 & Tier 0沈澱 -> 次回 Tier 0
        """
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph)

        # 1. 未知クエリ投入 (Level 3 フォールバック -> Tier 3)
        efp1 = BusinessInput(ticket_id="T_NEW_01", user_id="U1", category="workflow", query_text="出張精算の締日はいつ？")
        res1 = runtime.dispatch_ticket(efp1, human_override_answer="毎月末締め翌月10日払いです")
        self.assertEqual(res1.prediction.cost_tier, 3)

        # 2. 成功フィードバックを受領
        feedback = FeedbackResult(user_resolved=True, human_approved=True)
        res_fb = runtime.resolve_ticket_feedback("T_NEW_01", feedback)
        self.assertEqual(res_fb.status, CaseStatus.SUCCESS)
        self.assertTrue(res_fb.promoted_to_mb)

        # 3. 2回目の同一クエリディスパッチ -> Tier 0 で即答
        efp2 = BusinessInput(ticket_id="T_NEW_02", user_id="U2", category="workflow", query_text="出張精算の締日はいつ？")
        res2 = runtime.dispatch_ticket(efp2)
        self.assertEqual(res2.prediction.cost_tier, 0)
        self.assertIn("毎月末締め翌月10日払い", res2.final_output)

    def test_negative_outcomes_never_sediment_to_tier0(self):
        """
        受入条件 3: 失敗 (FAILURE) や 差し戻し (REJECTED) や タイムアウト (UNKNOWN) は決して Tier 0 に沈澱しない
        """
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph)
        norm_key = ("v1.0", "workflow", runtime.cascade._normalize("稟議申請の方法"))

        # ケースA: ユーザー未解決 (user_resolved=False -> FAILURE)
        efp_fail = BusinessInput(ticket_id="T_F01", user_id="U1", category="workflow", query_text="稟議申請の方法")
        runtime.dispatch_ticket(efp_fail)
        fb_fail = runtime.resolve_ticket_feedback("T_F01", FeedbackResult(user_resolved=False))
        self.assertEqual(fb_fail.status, CaseStatus.FAILURE)
        self.assertNotIn(norm_key, runtime.cascade.level0_cache)

        # 再ディスパッチしても Tier 0 にならず Tier 1 のまま
        res_after_fail = runtime.dispatch_ticket(BusinessInput("T_F02", "U2", "workflow", "稟議申請の方法"))
        self.assertEqual(res_after_fail.prediction.cost_tier, 1)

        # ケースB: 人間による差し戻し (human_rejected=True -> REJECTED)
        runtime.resolve_ticket_feedback("T_F02", FeedbackResult(user_resolved=True, human_rejected=True))
        self.assertNotIn(norm_key, runtime.cascade.level0_cache)

        res_after_rej = runtime.dispatch_ticket(BusinessInput("T_F03", "U3", "workflow", "稟議申請の方法"))
        self.assertEqual(res_after_rej.prediction.cost_tier, 1)

        # ケースC: タイムアウト未解決 (expire_pending_tickets -> UNKNOWN)
        runtime.expire_pending_tickets(["T_F03"])
        self.assertNotIn(norm_key, runtime.cascade.level0_cache)

        res_after_timeout = runtime.dispatch_ticket(BusinessInput("T_F04", "U4", "workflow", "稟議申請の方法"))
        self.assertEqual(res_after_timeout.prediction.cost_tier, 1)

    def test_canary_execution_never_pollutes_production_level0_cache(self):
        """
        受入条件 4: カナリア新本番での推論および成功確認は、旧本番の Level 0 キャッシュを汚染しない
        """
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph)
        prop = ReorganizationProposal(
            proposal_id="prop_canary_test",
            hot_node_id="node_wf_ringi",
            candidate_mb=self.candidate_graph,
            durability_test_result={"all_passed": True},
            policy=PromotionPolicy(require_durability=True, require_shadow=False, require_human_approval=True),
            status=ProposalState.APPROVAL_READY,
        )
        runtime.pending_reorganizations["prop_canary_test"] = prop

        mgr = AuthorityContext(actor_id="mgr_01", role="manager", scope="workflow", actor_type="human", authenticated_by="idp_sso")
        runtime.promote_candidate_mb("prop_canary_test", authority=mgr, use_canary=True, canary_ratio=1.0, theta_canary=2.0)

        # 1. カナリア環境でチケットを処理
        efp = BusinessInput(ticket_id="T_CANARY_01", user_id="U1", category="workflow", query_text="稟議申請の方法")
        res = runtime.dispatch_ticket(efp)
        self.assertTrue(res.is_canary)
        self.assertEqual(res.final_output, "新SaaSワークフローポータルから申請してください")

        # 2. カナリアで成功確認
        fb_res = runtime.resolve_ticket_feedback("T_CANARY_01", FeedbackResult(user_resolved=True))
        self.assertEqual(fb_res.status, CaseStatus.SUCCESS)

        # 3. 本番の cascade.level0_cache は一切汚染されていないこと！
        norm_key = ("v1.0", "workflow", runtime.cascade._normalize("稟議申請の方法"))
        self.assertNotIn(norm_key, runtime.cascade.level0_cache)

    def test_version_bump_or_rollback_invalidates_level0_cache(self):
        """
        受入条件 5: 本番昇格 (Leap) や ロールバック時に、旧バージョンの Level 0 キャッシュが全破棄・無効化されること
        """
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph)

        # 1. 成功確認によって Level 0 キャッシュに沈澱
        efp1 = BusinessInput("T01", "U1", "workflow", "稟議申請の方法")
        runtime.dispatch_ticket(efp1)
        runtime.resolve_ticket_feedback("T01", FeedbackResult(user_resolved=True))

        norm_key = ("v1.0", "workflow", runtime.cascade._normalize("稟議申請の方法"))
        self.assertIn(norm_key, runtime.cascade.level0_cache)

        # 次回ディスパッチが Tier 0 であることを確認
        res_tier0 = runtime.dispatch_ticket(BusinessInput("T02", "U2", "workflow", "稟議申請の方法"))
        self.assertEqual(res_tier0.prediction.cost_tier, 0)

        # 2. 一括昇格 (Leap) を実行
        prop = ReorganizationProposal(
            proposal_id="prop_leap_01",
            hot_node_id="node_wf_ringi",
            candidate_mb=self.candidate_graph,
            durability_test_result={"all_passed": True},
            policy=PromotionPolicy(require_durability=True, require_shadow=False, require_human_approval=True),
            status=ProposalState.APPROVAL_READY,
        )
        runtime.pending_reorganizations["prop_leap_01"] = prop
        mgr = AuthorityContext(actor_id="mgr_01", role="manager", scope="workflow", actor_type="human", authenticated_by="idp_sso")
        runtime.promote_candidate_mb("prop_leap_01", authority=mgr, use_canary=False)

        # 昇格により Level 0 キャッシュが無効化・クリアされていること！
        self.assertNotIn(norm_key, runtime.cascade.level0_cache)
        self.assertEqual(len(runtime.cascade.level0_cache), 0)

        # 新本番での初回クエリは Tier 0 ではなく新ルールによる Tier 1 で再評価されること
        res_new_prod = runtime.dispatch_ticket(BusinessInput("T03", "U3", "workflow", "稟議申請の方法"))
        self.assertEqual(res_new_prod.prediction.cost_tier, 1)
        self.assertEqual(res_new_prod.final_output, "新SaaSワークフローポータルから申請してください")

    def test_canary_timeout_never_pollutes_production_h_state(self):
        """受入条件 6 (P0): カナリア案件タイムアウト時に本番 HState が汚染されないこと"""
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph, theta_0=2.0)
        initial_prod_h = runtime.h_state.global_heat.total()
        initial_prod_theta = runtime.h_state.theta_eff("prod")

        prop = ReorganizationProposal(
            proposal_id="prop_canary_timeout",
            hot_node_id="node_wf_ringi",
            candidate_mb=self.candidate_graph,
            durability_test_result={"all_passed": True},
            policy=PromotionPolicy(require_durability=True, require_shadow=False, require_human_approval=True),
            status=ProposalState.APPROVAL_READY,
        )
        runtime.pending_reorganizations["prop_canary_timeout"] = prop
        mgr = AuthorityContext(actor_id="mgr_01", role="manager", scope="workflow", actor_type="human", authenticated_by="idp_sso")
        runtime.promote_candidate_mb("prop_canary_timeout", authority=mgr, use_canary=True, canary_ratio=1.0, theta_canary=2.0)

        efp = BusinessInput("T_CANARY_TIMEOUT", "U1", "workflow", "稟議申請の方法")
        disp_res = runtime.dispatch_ticket(efp)
        self.assertTrue(disp_res.is_canary)

        exp_res = runtime.expire_pending_tickets(["T_CANARY_TIMEOUT"])
        self.assertEqual(len(exp_res), 1)
        self.assertEqual(exp_res[0].status, CaseStatus.UNKNOWN)

        self.assertEqual(runtime.h_state.global_heat.total(), initial_prod_h)
        self.assertEqual(runtime.h_state.theta_eff("prod"), initial_prod_theta)
        self.assertEqual(runtime.h_state.total_tickets, 0)
        canary_ver = self.candidate_graph.version
        self.assertGreater(runtime.h_state.version_total_heat(canary_ver), 0.0)

    def test_authoritative_injection_vs_experiential_sedimentation_contract(self):
        """受入条件 7 (P1): 権限者による方針策定と事後成功確認による沈澱の分離"""
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph)

        # 1. 権限者指示 (HITL ゲート経由で即時方針策定)
        admin = AuthorityContext(actor_id="admin_01", role="admin", scope="all", actor_type="human", authenticated_by="idp_sso")
        efp_auth = BusinessInput("T_AUTH_01", "U1", "workflow", "SPECIAL_APPROVAL_ROUTE_QUERY")
        res_auth = runtime.dispatch_ticket(efp_auth, human_override_answer="SPECIAL_APPROVAL_POLICY_ANSWER", authority=admin)

        policy_nodes = [n for n in runtime.mb_graph.nodes.values() if n.authority_level == "policy"]
        self.assertEqual(len(policy_nodes), 1)
        self.assertIn("authority:admin:admin_01", policy_nodes[0].source_lineage)

        # 2. 一般ユーザー助言による経験沈澱 (SUCCESS 受領後に結晶化)
        efp_exp = BusinessInput("T_EXP_01", "U2", "workflow", "RECEIPT_REISSUE_QUERY")
        res_exp = runtime.dispatch_ticket(efp_exp, human_override_answer="RECEIPT_REISSUE_ANSWER")
        self.assertEqual(len([n for n in runtime.mb_graph.nodes.values() if "RECEIPT_REISSUE" in str(n.action_template)]), 0)

        runtime.resolve_ticket_feedback("T_EXP_01", FeedbackResult(user_resolved=True, human_approved=True))
        exp_nodes = [n for n in runtime.mb_graph.nodes.values() if "RECEIPT_REISSUE" in str(n.action_template)]
        self.assertEqual(len(exp_nodes), 1)
        self.assertEqual(exp_nodes[0].authority_level, "auto")
        self.assertIn("sedimentation:experience", exp_nodes[0].source_lineage)

    def test_version_bound_cache_identity(self):
        """受入条件 8 (P2/P0): Level 0 キャッシュの厳格なバージョン構造拘束"""
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph)
        efp = BusinessInput("T_VER_01", "U1", "workflow", "稟議申請の方法")
        runtime.dispatch_ticket(efp)
        runtime.resolve_ticket_feedback("T_VER_01", FeedbackResult(user_resolved=True))

        norm_q = runtime.cascade._normalize("稟議申請の方法")
        ver_key = ("v1.0", "workflow", norm_q)
        self.assertIn(ver_key, runtime.cascade.level0_cache)

        # 旧フォーマットの2タプルが仮にキャッシュ内に存在していても、
        # 異なるバージョン v99.0 では厳格にヒットしないこと（フォールバック禁止）
        other_graph = MBGraph(version="v99.0")
        other_cascade = runtime.cascade
        other_cascade.mb_graph = other_graph
        other_cascade.level0_cache[("workflow", norm_q)] = "stale_node"
        other_pred = other_cascade.interpret(efp)
        self.assertNotEqual(other_pred.cost_tier, 0)

    def test_unauthorized_policy_injection_fails_closed(self):
        """受入条件 9 (P2): 未認可アクターによる方針注入のフェイルクローズ遮断"""
        cascade = self.prod_graph and EnterpriseRuntime(mb_graph=self.prod_graph).cascade
        efp = BusinessInput("T_UNAUTH_01", "U1", "workflow", "UNAUTHORIZED_QUERY")

        # スコープ不一致のマネージャー
        wrong_scope_mgr = AuthorityContext(actor_id="mgr_net", role="manager", scope="network")
        with self.assertRaises(PermissionError):
            cascade.inject_authoritative_rule(
                efp=efp,
                policy_text="UNAUTHORIZED_RULE",
                category="workflow",
                authority=wrong_scope_mgr,
            )

        # 権限外ロール（オペレーター）
        operator = AuthorityContext(actor_id="op_01", role="operator", scope="all")
        with self.assertRaises(PermissionError):
            cascade.inject_authoritative_rule(
                efp=efp,
                policy_text="UNAUTHORIZED_RULE",
                category="workflow",
                authority=operator,
            )

    def test_timeout_never_increments_node_failure_or_degrades_confidence(self):
        """受入条件 10 (P0): TIMEOUT / UNKNOWN は node outcome failure から厳格分離され、観測保留として記録されること"""
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph)
        node = runtime.mb_graph.get("node_wf_ringi")
        initial_failure = node.failure_count
        initial_confidence = node.confidence
        initial_unresolved = node.unresolved_count

        # 1. 案件受付 -> タイムアウト (UNKNOWN化)
        efp_timeout = BusinessInput("T_TIMEOUT_TEST", "U1", "workflow", "稟議申請の方法")
        runtime.dispatch_ticket(efp_timeout)
        runtime.expire_pending_tickets(["T_TIMEOUT_TEST"])

        # TIMEOUT では failure_count や confidence は一切汚染されず、unresolved_count のみ加算されること
        self.assertEqual(node.failure_count, initial_failure)
        self.assertEqual(node.confidence, initial_confidence)
        self.assertEqual(node.unresolved_count, initial_unresolved + 1)

        # 2. 一方で明示的なユーザー未解決 (FAILURE) では failure_count++ / confidence-- となること
        efp_fail = BusinessInput("T_FAIL_TEST", "U2", "workflow", "稟議申請の方法")
        runtime.dispatch_ticket(efp_fail)
        runtime.resolve_ticket_feedback("T_FAIL_TEST", FeedbackResult(user_resolved=False))

        self.assertEqual(node.failure_count, initial_failure + 1)
        self.assertLess(node.confidence, initial_confidence)

    def test_legacy_cache_migration_requires_source_version_and_prevents_unauthorized_elevation(self):
        """受入条件 11 (P1): legacy cache migration は source-version を義務付け、現行バージョンへの自己昇格を防止すること"""
        from rdl_enterprise.cascade import InterpCascade

        graph = MBGraph(version="v2.0")

        # 1. 2タプルキーを initial_cache に渡すと ValueError で拒絶
        legacy_cache = {("workflow", "稟議申請の方法"): "node_wf_ringi"}
        with self.assertRaises(ValueError):
            InterpCascade(mb_graph=graph, initial_cache=legacy_cache)

        # 2. 2タプルキーを import_cache() に渡しても ValueError で拒絶
        cascade = InterpCascade(mb_graph=graph)
        with self.assertRaises(ValueError):
            cascade.import_cache(legacy_cache)

        # 3. 明示的な migrate_legacy_cache では source_mb_version が必須
        with self.assertRaises(ValueError):
            cascade.migrate_legacy_cache(legacy_cache, source_mb_version="")

        # 4. source_mb_version="v1.0" として安全に取り込み
        cascade.migrate_legacy_cache(legacy_cache, source_mb_version="v1.0")
        norm_q = cascade._normalize("稟議申請の方法")
        self.assertIn(("v1.0", "workflow", norm_q), cascade.level0_cache)
        self.assertNotIn(("v2.0", "workflow", norm_q), cascade.level0_cache)

        # カスケードの現行バージョンが v2.0 であるため、v1.0 由来のキャッシュは Level 0 でヒットしないこと！
        efp = BusinessInput("T_MIG_01", "U1", "workflow", "稟議申請の方法")
        pred = cascade.interpret(efp)
        self.assertNotEqual(pred.cost_tier, 0)

    def test_timeout_preserves_semantic_freshness_and_content_hash(self):
        """受入条件 12 (P0-P1): タイムアウト案件は意味的証拠鮮度 (last_evidence_at) と content_hash を保存し、観測時刻のみ記録すること"""
        from datetime import datetime, timezone, timedelta
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph)
        node = runtime.mb_graph.get("node_wf_ringi")

        # 過去の証拠更新時刻（100日前）に設定
        stale_evidence_time = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        node.last_support_at = stale_evidence_time
        node.last_observed_at = None
        initial_unresolved = node.unresolved_count

        initial_evidence_at = node.last_evidence_at
        initial_updated = node.last_updated
        initial_hash = runtime.mb_graph.content_hash()

        # 1. 案件受付 -> タイムアウト (UNKNOWN)
        efp_timeout = BusinessInput("T_TIMEOUT_FRESHNESS", "U1", "workflow", "稟議申請の方法")
        runtime.dispatch_ticket(efp_timeout)
        runtime.expire_pending_tickets(["T_TIMEOUT_FRESHNESS"])

        # 観測タイムスタンプおよび未解決カウントのみが更新されること
        self.assertIsNotNone(node.last_observed_at)
        self.assertEqual(node.unresolved_count, initial_unresolved + 1)

        # 意味的証拠鮮度 (last_evidence_at / last_updated) は一切更新・リフレッシュされていないこと
        self.assertEqual(node.last_evidence_at, initial_evidence_at)
        self.assertEqual(node.last_updated, initial_updated)

        # タイムアウト観測残差 ξ はグラフ同一性 (content_hash) に影響を与えないこと
        self.assertEqual(runtime.mb_graph.content_hash(), initial_hash)

        # 2. 一方で検証済みフィードバック (SUCCESS) では last_evidence_at が更新され、content_hash も変化すること
        efp_success = BusinessInput("T_SUCCESS_FRESHNESS", "U2", "workflow", "稟議申請の方法")
        runtime.dispatch_ticket(efp_success)
        runtime.resolve_ticket_feedback("T_SUCCESS_FRESHNESS", FeedbackResult(user_resolved=True, human_approved=True))

        self.assertNotEqual(node.last_evidence_at, initial_evidence_at)
        self.assertNotEqual(runtime.mb_graph.content_hash(), initial_hash)

    def test_evidence_polarity_separation_and_historical_opposing_signal(self):
        """受入条件 13 (P0-P1): 支持証拠と反証証拠の極性分離および歴史的反証シグナル (historical_opposing_signal) の独立性"""
        from datetime import datetime, timezone, timedelta
        from rdl_enterprise.constraint import RelationConstraintLocator, ConstraintContext
        from rdl_enterprise.snapshot import FrozenInterpretationContext

        runtime = EnterpriseRuntime(mb_graph=self.prod_graph)
        node = runtime.mb_graph.get("node_wf_ringi")

        # 過去の支持証拠時刻（250日前: half_life=90日なので約0.14）に設定、反証証拠は None
        stale_support_time = (datetime.now(timezone.utc) - timedelta(days=250)).isoformat()
        node.last_support_at = stale_support_time
        node.last_opposing_at = None
        node.last_observed_at = None

        locator = RelationConstraintLocator()
        efp_q = BusinessInput("T_POLARITY_01", "U1", "workflow", "稟議申請の方法")
        ctx_before = ConstraintContext(
            efp=efp_q,
            current_time=datetime.now(timezone.utc),
            mb_version=self.prod_graph.version,
            active_domain="workflow",
        )
        bundle_before = locator.locate_bundle_for_node(runtime.mb_graph, node, ctx_before)
        initial_support_freshness = bundle_before.freshness
        initial_opposing_signal = bundle_before.historical_opposing_signal
        self.assertLess(initial_support_freshness, 0.2)  # 250日前なので陳腐化
        self.assertEqual(initial_opposing_signal, 0.0)  # 反証実績なし

        # 1. 案件受付 -> 失敗・差し戻し (FAILURE / REJECTED)
        runtime.dispatch_ticket(efp_q)
        runtime.resolve_ticket_feedback("T_POLARITY_01", FeedbackResult(user_resolved=False, human_rejected=True))

        # 反証極性 (OPPOSE) の記録確認
        self.assertEqual(node.failure_count, 1)
        self.assertEqual(node.rejection_count, 1)
        self.assertIsNotNone(node.last_opposing_at)
        opposing_at_after_failure = node.last_opposing_at

        # 支持極性 (SUPPORT) は不変（250日前のまま）であること
        self.assertEqual(node.last_support_at, stale_support_time)

        # 失敗により支持鮮度 (Core freshness) が不当に上昇していないことの確認！
        bundle_after_failure = locator.locate_bundle_for_node(runtime.mb_graph, node, ctx_before)
        self.assertEqual(bundle_after_failure.freshness, initial_support_freshness)

        # 一方で歴史的反証シグナル (historical_opposing_signal) は大きく立ち上がっていること
        self.assertGreater(bundle_after_failure.opposing_freshness, 0.9)
        self.assertGreater(bundle_after_failure.historical_opposing_signal, 1.0)

        # 2. タイムアウト (UNKNOWN) の観測保留
        efp_q2 = BusinessInput("T_POLARITY_02", "U2", "workflow", "稟議申請の方法")
        runtime.dispatch_ticket(efp_q2)
        runtime.expire_pending_tickets(["T_POLARITY_02"])

        # 観測タイムスタンプのみ更新され、支持証拠・反証証拠のタイムスタンプは保存されること
        self.assertEqual(node.last_support_at, stale_support_time)
        self.assertEqual(node.last_opposing_at, opposing_at_after_failure)

        # 3. 成功確認 (SUCCESS) による支持証拠の更新
        efp_q3 = BusinessInput("T_POLARITY_03", "U3", "workflow", "稟議申請の方法")
        runtime.dispatch_ticket(efp_q3)
        runtime.resolve_ticket_feedback("T_POLARITY_03", FeedbackResult(user_resolved=True, human_approved=True))

        # 支持極性 (SUPPORT) が現在時刻に更新され、Core freshness が回復すること
        self.assertNotEqual(node.last_support_at, stale_support_time)
        bundle_after_success = locator.locate_bundle_for_node(runtime.mb_graph, node, ctx_before)
        self.assertGreater(bundle_after_success.freshness, 0.9)

        # 4. 対向のみノード (last_support_at=None, last_opposing_at=now) のフォールバック遮断検査
        opposing_only_node = MBNode(
            id="node_opposing_only",
            domain="workflow",
            trigger_pattern={"exact_keys": ["テスト失敗案件"]},
            action_template={"type": "direct_reply", "payload": "NG"},
            last_support_at=None,
            last_opposing_at=datetime.now(timezone.utc).isoformat(),
        )
        runtime.mb_graph.commit_node(opposing_only_node, origin=CommitmentOrigin.TEST_FIXTURE)
        bundle_opp_only = locator.locate_bundle_for_node(runtime.mb_graph, opposing_only_node, ctx_before)
        # フォールバック抜け穴が塞がれ、支持鮮度が厳格に 0.0 であること！
        self.assertEqual(bundle_opp_only.freshness, 0.0)
        self.assertGreater(bundle_opp_only.opposing_freshness, 0.9)

        # 5. 読み取り専用セッター契約検査 (AttributeError 送出)
        with self.assertRaises(AttributeError):
            node.last_evidence_at = datetime.now(timezone.utc).isoformat()
        with self.assertRaises(AttributeError):
            node.last_updated = datetime.now(timezone.utc).isoformat()

        # 6. 実績ゼロのレガシーノードのフェイルクローズ検査 (勝手な正極性捏造の排除)
        legacy_node = MBNode(
            id="node_legacy_unverified",
            domain="workflow",
            trigger_pattern={"exact_keys": ["レガシー案件"]},
            action_template={"type": "direct_reply", "payload": "legacy"},
            last_updated="2026-09-01T00:00:00Z",
            success_count=0,
            failure_count=0,
            approval_count=0,
            rejection_count=0,
        )
        self.assertIsNone(legacy_node.last_support_at)
        self.assertIsNone(legacy_node.last_opposing_at)
        self.assertEqual(legacy_node.legacy_evidence_at, "2026-09-01T00:00:00Z")

        # 7. mixed legacy history (success + failure) のフェイルクローズ検査 (両方混在時は極性推定を完全排除)
        mixed_legacy_node = MBNode(
            id="node_legacy_mixed",
            domain="workflow",
            trigger_pattern={"exact_keys": ["混在レガシー案件"]},
            action_template={"type": "direct_reply", "payload": "mixed"},
            last_updated="2026-09-01T00:00:00Z",
            success_count=3,
            failure_count=2,
            approval_count=1,
            rejection_count=1,
        )
        self.assertIsNone(mixed_legacy_node.last_support_at)
        self.assertIsNone(mixed_legacy_node.last_opposing_at)
        self.assertEqual(mixed_legacy_node.legacy_evidence_at, "2026-09-01T00:00:00Z")

        runtime.mb_graph.commit_node(mixed_legacy_node, origin=CommitmentOrigin.MIGRATION_VERIFIED)
        bundle_mixed = locator.locate_bundle_for_node(runtime.mb_graph, mixed_legacy_node, ctx_before)
        self.assertEqual(bundle_mixed.freshness, 0.0)
        self.assertEqual(bundle_mixed.opposing_freshness, 0.0)

        # 8. policy + failure / approval + failure 等の混在もフェイルクローズされること
        policy_failed_node = MBNode(
            id="node_policy_failed",
            domain="workflow",
            trigger_pattern={"exact_keys": ["ポリシー違反案件"]},
            action_template={"type": "direct_reply", "payload": "policy_fail"},
            authority_level="policy",
            failure_count=1,
            last_updated="2026-09-01T00:00:00Z",
        )
        self.assertIsNone(policy_failed_node.last_support_at)
        self.assertIsNone(policy_failed_node.last_opposing_at)
        self.assertEqual(policy_failed_node.legacy_evidence_at, "2026-09-01T00:00:00Z")

    def test_description_vs_commitment_lifecycle(self):
        """
        Acceptance Test 14: 認知的ライフサイクル (Description -> Commitment -> Active Constraint)
        (BASE v2.0 §4.2: 関係記述 != 正統コミットメント != 活性化拘束)

        1. 純粋な Python MBNode(...) 記述オブジェクトの生成時点では正の支持証拠を一切捏造しない
           (last_support_at is None, last_evidence_at is None, freshness == 0.0)
        2. MBGraph.commit_node() ゲートウェイを通過して初めて、明示的な CommitmentOrigin と支持証拠打刻が付与される
        3. origin='authority' の場合、AuthorityContext が無効または未認可であれば PermissionError で遮断 (Fail-Closed)
        4. 認可された権威コミットまたは経験沈澱 (origin='experience') を経て初めて、活性化拘束・鮮度回復が認められる
        """
        from datetime import datetime, timezone
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin, CommitmentRecord, IntegrityError, LegacySnapshot, MigrationContext
        from rdl_enterprise.authority import AuthorityContext
        from rdl_enterprise.constraint import RelationConstraintLocator, ConstraintContext
        from rdl_enterprise.snapshot import BusinessInput

        graph = MBGraph()

        # 1. 記述フェーズ (Description Phase): 単なる MBNode のインスタンス化
        desc_node = MBNode(
            id="node_desc_candidate",
            domain="security",
            trigger_pattern={"exact_keys": ["パスワード変更"]},
            action_template={"type": "direct_reply", "payload": "8文字以上で設定してください"},
            authority_level="auto",
        )
        # 支持証拠・確定証拠は一切持たず、None (ξ) であること
        self.assertIsNone(desc_node.last_support_at)
        self.assertIsNone(desc_node.last_opposing_at)
        self.assertIsNone(desc_node.last_evidence_at)
        self.assertIsNone(desc_node.commitment_origin)

        # 記述ノードをグラフに直接 add_or_update することは ValueError で即座に拒絶されること (Fail-Closed)
        with self.assertRaises(ValueError):
            graph.add_or_update(desc_node)

        # 記述ノード単体では拘束解決から排除され None (Fail-Closed)
        locator = RelationConstraintLocator()
        efp = BusinessInput("T_DESC_01", "U_SEC", "security", "パスワード変更のルール")
        ctx = ConstraintContext(efp=efp, current_time=datetime.now(timezone.utc), active_domain="security")
        bundle_uncommitted = locator.locate_bundle_for_node(graph, desc_node, ctx)
        self.assertIsNone(bundle_uncommitted)

        # Constructor Forgery 遮断検査: 外部直接引数でコミットメントを偽装しようとしても無視・無効化されること (P0)
        forged_node = MBNode(
            id="node_forged",
            domain="security",
            trigger_pattern={"exact_keys": ["不正コミット"]},
            action_template={"type": "direct_reply", "payload": "evil"},
            commitment_origin="authority",
            committed_at="2026-09-01T00:00:00Z",
            commitment_record={"origin": "authority", "committed_at": "2026-09-01T00:00:00Z", "actor": "hacker"},
        )
        self.assertFalse(forged_node.is_committed)
        self.assertIsNone(forged_node.commitment_origin)
        self.assertIsNone(forged_node.committed_at)
        self.assertIsNone(forged_node.commitment_record)
        with self.assertRaises(ValueError):
            graph.add_or_update(forged_node)

        # _internal_commitment 引数の完全撤去検査 (新P0)
        with self.assertRaises(TypeError):
            MBNode(
                id="node_internal_forged",
                domain="security",
                trigger_pattern={"exact_keys": ["内部偽装"]},
                action_template={"type": "direct_reply", "payload": "evil"},
                _internal_commitment=CommitmentRecord(origin="authority", committed_at="2026-09-01T00:00:00Z", actor="hacker"),
            )

        # 属性イミュータビリティ検査: コミットメント属性および _commitment_record の直接代入は AttributeError で拒絶されること (P0-P1)
        with self.assertRaises(AttributeError):
            desc_node.commitment_origin = "authority"
        with self.assertRaises(AttributeError):
            desc_node.committed_at = "2026-09-01T00:00:00Z"
        with self.assertRaises(AttributeError):
            desc_node.commitment_record = {}
        with self.assertRaises(AttributeError):
            desc_node._commitment_record = CommitmentRecord(origin="authority", committed_at="2026-09-01T00:00:00Z", actor="hacker")

        # Cascade レベルでも未コミット記述ノードは推論・活性化サブグラフから 100% 排除されること
        from rdl_enterprise.cascade import InterpCascade
        cascade = InterpCascade(graph)
        pred_uncommitted = cascade.interpret(efp)
        # マッチせずフォールバックへ進むこと
        self.assertNotEqual(pred_uncommitted.matched_node_id, "node_desc_candidate")
        self.assertEqual(pred_uncommitted.action_type, "ask_human")

        # 2. 権威コミット時の認可チェック (origin='authority')
        # (a) AuthorityContext なしのコミットは PermissionError
        with self.assertRaises(PermissionError):
            graph.commit_node(desc_node, origin=CommitmentOrigin.AUTHORITY, authority_context=None)

        # (b) ドメイン管轄外 (finance 担当者が security をコミット) の権威コミットは PermissionError
        unauthorized_auth = AuthorityContext(actor_id="finance_lead", role="manager", scope="finance")
        with self.assertRaises(PermissionError):
            graph.commit_node(desc_node, origin=CommitmentOrigin.AUTHORITY, authority_context=unauthorized_auth)

        # (c) 未知の origin 文字列は ValueError で拒絶
        with self.assertRaises(ValueError):
            graph.commit_node(desc_node, origin="unknown_origin")

        # 3. 正当な権威コミット (security 管理者によるコミット: role in ('admin', 'manager'))
        authorized_auth = AuthorityContext(actor_id="ciso_admin", role="manager", scope="security")
        committed_policy = graph.commit_node(
            desc_node,
            origin=CommitmentOrigin.AUTHORITY,
            authority_context=authorized_auth,
        )
        self.assertEqual(committed_policy.commitment_origin, "authority")
        self.assertEqual(committed_policy.authority_level, "policy")
        self.assertEqual(committed_policy.source_id, "ciso_admin")
        self.assertIn("authority:manager:ciso_admin", committed_policy.source_lineage)
        self.assertIsNotNone(committed_policy.last_support_at)
        self.assertIsNotNone(committed_policy.last_evidence_at)
        self.assertIsNotNone(committed_policy.committed_at)
        self.assertIsNotNone(committed_policy.commitment_record)
        self.assertEqual(committed_policy.commitment_record["origin"], "authority")

        # コミット済みノードの再コミット禁止検査 (単一コミットモデル: 来歴の上書き封殺)
        with self.assertRaises(ValueError):
            graph.commit_node(committed_policy, origin=CommitmentOrigin.AUTHORITY, authority_context=authorized_auth)

        # コミット後は正統な支持証拠打刻により freshness が健全に回復し、推論で自律回答可能となる
        bundle_committed = locator.locate_bundle_for_node(graph, committed_policy, ctx)
        self.assertGreater(bundle_committed.freshness, 0.9)
        pred_committed = cascade.interpret(efp)
        self.assertEqual(pred_committed.matched_node_id, "node_desc_candidate")
        self.assertEqual(pred_committed.cost_tier, 1)

        # 4. 経験沈澱コミット (origin='experience')
        exp_node = MBNode(
            id="node_exp_candidate",
            domain="security",
            trigger_pattern={"exact_keys": ["MFA設定"]},
            action_template={"type": "direct_reply", "payload": "Authenticatorアプリを利用してください"},
        )
        self.assertIsNone(exp_node.last_support_at)
        committed_exp = graph.commit_node(exp_node, origin=CommitmentOrigin.VERIFIED_EXPERIENCE)
        self.assertEqual(committed_exp.commitment_origin, "experience")
        self.assertIn("sedimentation:experience", committed_exp.source_lineage)
        self.assertIsNotNone(committed_exp.last_support_at)

        # 5. content_hash への包含確認（コミットメント証跡改ざん検知）
        h1 = graph.content_hash()
        # 別のコミット出所を持つノードを追加するとハッシュが厳格に変化すること
        fixture_node = MBNode(
            id="node_fixture",
            domain="security",
            trigger_pattern={"exact_keys": ["テスト用"]},
            action_template={"type": "direct_reply", "payload": "fixture"},
        )
        graph.commit_node(fixture_node, origin=CommitmentOrigin.TEST_FIXTURE)
        h2 = graph.content_hash()
        self.assertNotEqual(h1, h2)

        # 6. CommitmentRecord.from_dict_strict 厳格検証検査 (P0: Serialized-data forgery 排除)
        # (a) 必須フィールド欠損
        with self.assertRaises(ValueError):
            CommitmentRecord.from_dict_strict({"actor": "someone"})
        # (b) 未知の origin
        with self.assertRaises(ValueError):
            CommitmentRecord.from_dict_strict({"origin": "bogus", "committed_at": "2026-09-01T00:00:00Z", "actor": "someone"})
        # (c) 不正な ISO-8601 時刻文字列
        with self.assertRaises(ValueError):
            CommitmentRecord.from_dict_strict({"origin": "authority", "committed_at": "invalid_date", "actor": "someone"})
        # (d) 外側フィールドとの不一致
        with self.assertRaises(ValueError):
            CommitmentRecord.from_dict_strict(
                {"origin": "authority", "committed_at": "2026-09-01T00:00:00Z", "actor": "someone"},
                outer_origin="experience",
            )

        # 7. ロード時 content_hash 完全性照合検査 (P1: Load-time Integrity Verification)
        saved_dict = graph.to_dict()
        self.assertIn("content_hash", saved_dict)
        # 正常時はロード成功
        loaded_ok = MBGraph.from_dict(saved_dict, verify_hash=True)
        self.assertEqual(loaded_ok.content_hash(), saved_dict["content_hash"])

        # ペイロード改変＋旧ハッシュ残存（改ざん）時は IntegrityError で即座にロード拒絶
        tampered_saved = dict(saved_dict)
        tampered_saved["nodes"] = dict(saved_dict["nodes"])
        tampered_saved["nodes"]["node_desc_candidate"] = dict(saved_dict["nodes"]["node_desc_candidate"])
        tampered_saved["nodes"]["node_desc_candidate"]["action_template"] = {"type": "direct_reply", "payload": "tampered_evil"}
        with self.assertRaises(IntegrityError):
            MBGraph.from_dict(tampered_saved, verify_hash=True)

        # 8. デシリアライズ・復元のフェイルクローズ検査 (P0) & 明示的移行ゲートウェイ (P2)
        uncommitted_data = {
            "version": "v1.0",
            "nodes": {
                "n_uncommitted": {
                    "id": "n_uncommitted",
                    "domain": "security",
                    "trigger_pattern": {"exact_keys": ["旧データ"]},
                    "action_template": {"type": "direct_reply", "payload": "legacy"},
                }
            }
        }
        # 未コミットノードを含むデータの復元はデフォルトで拒絶されること (勝手な自動昇格の根絶)
        with self.assertRaises(ValueError):
            MBGraph.from_dict(uncommitted_data, allow_uncommitted=False)

        # allow_uncommitted=True で読み込み後、真正な LegacySnapshot + MigrationContext を用いた移行検証
        legacy_graph = MBGraph.from_dict(uncommitted_data, allow_uncommitted=True)
        self.assertFalse(legacy_graph.get("n_uncommitted").is_committed)

        # (0) 互換ショートカット（文字列引数）の廃絶確認: TypeError が発生すること
        with self.assertRaises(TypeError):
            legacy_graph.migrate_legacy_nodes(source_version="v0.9", migrated_by="sec_admin")

        snap = LegacySnapshot(
            source_version="v0.9",
            raw_payload=uncommitted_data,
            expected_source_hash=LegacySnapshot("v0.9", uncommitted_data).compute_hash(),
        )

        # (a) 権限外アクターによる移行試行は PermissionError
        unauth_mig_ctx = MigrationContext(verifier_id="guest_user", role="guest")
        with self.assertRaises(PermissionError):
            legacy_graph.migrate_legacy_nodes(snapshot=snap, context=unauth_mig_ctx)

        # (b) ハッシュ不一致（改ざんスナップショット）による移行試行は IntegrityError
        tampered_snap = LegacySnapshot(
            source_version="v0.9",
            raw_payload=uncommitted_data,
            expected_source_hash="0000000000000000000000000000000000000000000000000000000000000000",
        )
        auth_mig_ctx = MigrationContext(verifier_id="sec_admin", role="admin")
        with self.assertRaises(IntegrityError):
            legacy_graph.migrate_legacy_nodes(snapshot=tampered_snap, context=auth_mig_ctx)

        # (b-2) 対象ノード不一致（すり替え・過不足）による移行試行は IntegrityError
        mismatched_payload = {
            "nodes": {
                "n_other": {
                    "id": "n_other",
                    "domain": "security",
                    "trigger_pattern": {"exact_keys": ["旧データ"]},
                    "action_template": {"type": "direct_reply", "payload": "legacy"},
                }
            }
        }
        mismatched_snap = LegacySnapshot(
            source_version="v0.9",
            raw_payload=mismatched_payload,
            expected_source_hash=LegacySnapshot("v0.9", mismatched_payload).compute_hash(),
        )
        with self.assertRaises(IntegrityError):
            legacy_graph.migrate_legacy_nodes(snapshot=mismatched_snap, context=auth_mig_ctx)

        # (c) 正当な Snapshot + Context による検証済み移行の成功
        migrated_count = legacy_graph.migrate_legacy_nodes(snapshot=snap, context=auth_mig_ctx)
        self.assertEqual(migrated_count, 1)
        migrated_node = legacy_graph.get("n_uncommitted")
        self.assertTrue(migrated_node.is_committed)
        self.assertEqual(migrated_node.commitment_origin, "migration")
        self.assertEqual(migrated_node.commitment_record["actor"], "sec_admin")
        self.assertIn("migration:v0.9:sec_admin", migrated_node.commitment_record["lineage"])


if __name__ == "__main__":
    unittest.main()

