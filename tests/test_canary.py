import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from rdl_enterprise.mb_graph import MBGraph, MBNode
from rdl_enterprise.snapshot import BusinessInput, FeedbackResult
from rdl_enterprise.authority import AuthorityContext
from rdl_enterprise.promotion_gate import ProposalState, PromotionPolicy
from rdl_enterprise.runtime import EnterpriseRuntime, ReorganizationProposal
from rdl_enterprise.h_state import HeatVector
from rdl_enterprise.canary import CanaryStatus, CanaryManager, CanaryCompletionPolicy


class TestCanaryDeploymentAndRollback(unittest.TestCase):

    def setUp(self):
        # 旧本番 M_B
        self.prod_graph = MBGraph()
        self.prod_graph.add_or_update(MBNode(
            id="node_wf",
            domain="workflow",
            trigger_pattern={"exact_keys": ["稟議申請"]},
            action_template={"type": "direct_reply", "payload": "http://old-legacy.corp"},
            confidence=0.8,
        ))

        # 昇格候補 M_B'
        self.candidate_graph = MBGraph()
        self.candidate_graph.add_or_update(MBNode(
            id="node_wf",
            domain="workflow",
            trigger_pattern={"exact_keys": ["稟議申請"]},
            action_template={"type": "direct_reply", "payload": "https://new-saas.corp"},
            confidence=0.9,
        ))

    def test_canary_routing_and_step_up_to_completion(self):
        """カナリア展開: 段階的配分拡大から全面展開完了 (Full Commit) までの正常系"""
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph, theta_0=1.0)
        prop = ReorganizationProposal(
            proposal_id="prop_canary_01",
            hot_node_id="node_wf",
            candidate_mb=self.candidate_graph,
            durability_test_result={"all_passed": True},
            policy=PromotionPolicy(require_durability=True, require_shadow=False, require_human_approval=True),
            status=ProposalState.APPROVAL_READY,
        )
        runtime.pending_reorganizations["prop_canary_01"] = prop

        mgr = AuthorityContext(
            actor_id="mgr_01",
            role="manager",
            scope="workflow",
            actor_type="human",
            authenticated_by="idp_sso",
        )

        # 1. カナリア展開を開始 (初期比率 100% で検証のため固定)
        success = runtime.promote_candidate_mb(
            "prop_canary_01",
            authority=mgr,
            use_canary=True,
            canary_ratio=1.0,
            theta_canary=2.0,
        )
        self.assertTrue(success)
        self.assertIsNotNone(runtime.canary_manager.active_deployment)
        self.assertEqual(runtime.canary_manager.active_deployment.status, CanaryStatus.ACTIVE)

        # 2. カナリア新本番でチケットを処理
        efp = BusinessInput("T_CANARY_01", "U1", "workflow", "稟議申請")
        res = runtime.dispatch_ticket(efp)
        self.assertTrue(res.is_canary)
        self.assertEqual(res.final_output, "https://new-saas.corp")

        # 3. 正常解決フィードバックを受領
        fb_res = runtime.resolve_ticket_feedback("T_CANARY_01", FeedbackResult(user_resolved=True))
        self.assertFalse(fb_res.canary_rolled_back)
        self.assertEqual(runtime.canary_manager.active_deployment.canary_success_count, 1)

        # 4. 全面展開完了 (100% コミット)
        complete_success = runtime.complete_canary_rollout()
        self.assertTrue(complete_success)
        self.assertIsNone(runtime.canary_manager.active_deployment)
        # 本番グラフが新URLへ恒久置換された
        self.assertEqual(runtime.mb_graph.get("node_wf").action_template["payload"], "https://new-saas.corp")

    def test_canary_automated_rollback_on_excessive_heat(self):
        """異常発熱による自動ロールバック: カナリア新本番で差し戻し・破断が発生した場合、旧本番へ即時自動復元"""
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph, theta_0=1.0)
        prop = ReorganizationProposal(
            proposal_id="prop_broken_02",
            hot_node_id="node_wf",
            candidate_mb=self.candidate_graph,
            durability_test_result={"all_passed": True},
            policy=PromotionPolicy(require_durability=True, require_shadow=False, require_human_approval=True),
            status=ProposalState.APPROVAL_READY,
        )
        runtime.pending_reorganizations["prop_broken_02"] = prop

        mgr = AuthorityContext(
            actor_id="mgr_01",
            role="manager",
            scope="workflow",
            actor_type="human",
            authenticated_by="idp_sso",
        )

        # カナリア展開開始 (許容熱 θ_canary = 1.0, 許容失敗 = 0)
        runtime.promote_candidate_mb(
            "prop_broken_02",
            authority=mgr,
            use_canary=True,
            canary_ratio=1.0,
            theta_canary=1.0,
            max_canary_failures=0,
        )

        # チケット処理
        efp = BusinessInput("T_BROKEN_01", "U2", "workflow", "稟議申請")
        res = runtime.dispatch_ticket(efp)
        self.assertTrue(res.is_canary)

        # ユーザーおよび管理者が「新URLは致命的なエラー！」と差し戻し (human_rejected)
        feedback = FeedbackResult(user_resolved=False, human_rejected=True)
        fb_res = runtime.resolve_ticket_feedback("T_BROKEN_01", feedback)

        # 自動ロールバックが即時発動した！
        self.assertTrue(fb_res.canary_rolled_back)
        self.assertIn("カナリア", fb_res.canary_rollback_reason)
        self.assertIsNone(runtime.canary_manager.active_deployment)

        # 旧本番グラフに安全に復元されている！
        self.assertEqual(runtime.mb_graph.get("node_wf").action_template["payload"], "http://old-legacy.corp")

        # 履歴にロールバック理由が記録されている
        self.assertEqual(len(runtime.reorganization_history), 1)
        self.assertEqual(runtime.reorganization_history[0].status, ProposalState.REGRESSED)

    def test_canary_manual_rollback(self):
        """手動ロールバック: 運用担当者による手動ロールバック指示で即時復元"""
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph, theta_0=1.0)
        prop = ReorganizationProposal(
            proposal_id="prop_manual_03",
            hot_node_id="node_wf",
            candidate_mb=self.candidate_graph,
            durability_test_result={"all_passed": True},
            policy=PromotionPolicy(require_durability=True, require_shadow=False, require_human_approval=True),
            status=ProposalState.APPROVAL_READY,
        )
        runtime.pending_reorganizations["prop_manual_03"] = prop

        mgr = AuthorityContext(
            actor_id="mgr_01",
            role="manager",
            scope="workflow",
            actor_type="human",
            authenticated_by="idp_sso",
        )

        runtime.promote_candidate_mb("prop_manual_03", authority=mgr, use_canary=True, canary_ratio=0.5)
        self.assertIsNotNone(runtime.canary_manager.active_deployment)

    def test_canary_heat_does_not_pollute_prod_h_state(self):
        """Version-aware 熱管理: カナリア新本番で発生した熱が旧本番 M_B の熱状態を汚染しないこと"""
        self.prod_graph.version = "v1.0"
        self.candidate_graph.version = "v2.0"
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph, theta_0=5.0)

        prop = ReorganizationProposal(
            proposal_id="prop_heat_iso",
            hot_node_id="node_wf",
            candidate_mb=self.candidate_graph,
            durability_test_result={"all_passed": True},
            policy=PromotionPolicy(require_durability=True, require_shadow=False, require_human_approval=True),
            status=ProposalState.APPROVAL_READY,
        )
        runtime.pending_reorganizations["prop_heat_iso"] = prop

        mgr = AuthorityContext(
            actor_id="mgr_01",
            role="manager",
            scope="workflow",
            actor_type="human",
            authenticated_by="idp_sso",
        )

        # カナリア展開を開始 (許容熱を大きめにしてロールバックさせずに熱の行方をテスト)
        runtime.promote_candidate_mb(
            "prop_heat_iso",
            authority=mgr,
            use_canary=True,
            canary_ratio=1.0,
            theta_canary=100.0,
            max_canary_failures=10,
        )

        # カナリア案件を処理 (query_text を5文字以上にして入力不整合熱を防ぐ)
        efp = BusinessInput("T_CANARY_ERR_01", "U1", "workflow", "稟議申請の実行")
        runtime.dispatch_ticket(efp)

        # 失敗フィードバックを注入 (差し戻し)
        runtime.resolve_ticket_feedback("T_CANARY_ERR_01", FeedbackResult(user_resolved=False, human_rejected=True))

        # 1. 本番グローバル熱および本番ノード熱がゼロのままであること（汚染されていない）
        self.assertEqual(runtime.h_state.global_heat.total(), 0.0)
        self.assertEqual(runtime.h_state.node_heats.get("node_wf", HeatVector()).total(), 0.0)

        # 2. カナリア版 (v2.0) のバージョン付き熱のみに蓄積されていること
        canary_node_heat = runtime.h_state.get_heat_for_version("node_wf", "v2.0")
        self.assertGreater(canary_node_heat.total(), 0.0)

    def test_canary_candidate_immutability_and_prod_isolation(self):
        """候補の不変性(Freeze)と旧本番の隔離: カナリア中の成功や回答によって候補 M_B' も旧本番 M_B も一切変質しないこと (公理B5)"""
        self.prod_graph.version = "v1.0"
        self.candidate_graph.version = "v2.0"
        initial_cand_hash = self.candidate_graph.content_hash()
        initial_prod_hash = self.prod_graph.content_hash()

        runtime = EnterpriseRuntime(mb_graph=self.prod_graph, theta_0=5.0)

        prop = ReorganizationProposal(
            proposal_id="prop_learn_iso",
            hot_node_id="node_wf",
            candidate_mb=self.candidate_graph,
            durability_test_result={"all_passed": True},
            policy=PromotionPolicy(require_durability=True, require_shadow=False, require_human_approval=True),
            status=ProposalState.APPROVAL_READY,
        )
        runtime.pending_reorganizations["prop_learn_iso"] = prop

        mgr = AuthorityContext(
            actor_id="mgr_01",
            role="manager",
            scope="workflow",
            actor_type="human",
            authenticated_by="idp_sso",
        )

        runtime.promote_candidate_mb("prop_learn_iso", authority=mgr, use_canary=True, canary_ratio=1.0)

        # カナリア候補グラフが Freeze されていること
        self.assertTrue(runtime.canary_manager.active_deployment.canary_mb.is_frozen)

        # 未学習パターンの入力をカナリアでディスパッチ (人間オペレーターの回答付き)
        efp = BusinessInput("T_CANARY_LEARN_01", "U1", "workflow", "稟議の例外ルート申請")
        runtime.dispatch_ticket(efp, human_override_answer="特別ルート案内", authority=mgr)

        # 成功解決フィードバック
        runtime.resolve_ticket_feedback("T_CANARY_LEARN_01", FeedbackResult(user_resolved=True))

        # 1. 旧本番グラフに新ルールが沈澱・汚染されていないこと、かつハッシュ不変
        self.assertNotIn("node_learned_002", runtime.mb_graph.nodes)
        self.assertEqual(runtime.mb_graph.content_hash(), initial_prod_hash)

        # 2. カナリア候補グラフ側も完全 Freeze が維持され、勝手に変質(M_B''化)していないこと
        canary_graph = runtime.canary_manager.active_deployment.canary_mb
        self.assertEqual(canary_graph.content_hash(), initial_cand_hash)

    def test_canary_completion_policy_blocks_premature_commit(self):
        """エビデンスゲート: 0件処理や基準未達でのコミット試行が確実に拒否されること"""
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph, theta_0=1.0)
        prop = ReorganizationProposal(
            proposal_id="prop_evidence_01",
            hot_node_id="node_wf",
            candidate_mb=self.candidate_graph,
            durability_test_result={"all_passed": True},
            policy=PromotionPolicy(require_durability=True, require_shadow=False, require_human_approval=True),
            status=ProposalState.APPROVAL_READY,
        )
        runtime.pending_reorganizations["prop_evidence_01"] = prop

        mgr = AuthorityContext(
            actor_id="mgr_01",
            role="manager",
            scope="workflow",
            actor_type="human",
            authenticated_by="idp_sso",
        )
        runtime.promote_candidate_mb("prop_evidence_01", authority=mgr, use_canary=True, canary_ratio=1.0)

        # ポリシー: 最低5件、成功3件以上を要求
        policy = CanaryCompletionPolicy(minimum_cases=5, minimum_successes=3)

        # 1. まだ1件も処理していない段階での完了コミットは拒否される
        success = runtime.complete_canary_rollout(policy=policy)
        self.assertFalse(success)
        self.assertIsNotNone(runtime.canary_manager.active_deployment)

        # 2. 2件成功処理 (まだ5件未満)
        for i in range(2):
            tid = f"T_EVID_{i}"
            efp = BusinessInput(tid, "U1", "workflow", "稟議申請の承認")
            runtime.dispatch_ticket(efp)
            runtime.resolve_ticket_feedback(tid, FeedbackResult(user_resolved=True))

        success2 = runtime.complete_canary_rollout(policy=policy)
        self.assertFalse(success2)

        # 3. さらに3件成功処理 (計5件成功 >= 5件、成功5 >= 3)
        for i in range(2, 5):
            tid = f"T_EVID_{i}"
            efp = BusinessInput(tid, "U1", "workflow", "稟議申請の承認")
            runtime.dispatch_ticket(efp)
            runtime.resolve_ticket_feedback(tid, FeedbackResult(user_resolved=True))

        # 基準を満たしたのでコミット成功！
        success3 = runtime.complete_canary_rollout(policy=policy)
        self.assertTrue(success3)
        self.assertIsNone(runtime.canary_manager.active_deployment)

    def test_action_ledger_triggers_compensating_action_on_rollback(self):
        """外界作用の追跡と補償: カナリアロールバック時に ActionLedger が補償アクションを実行・記録すること"""
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph, theta_0=1.0)
        prop = ReorganizationProposal(
            proposal_id="prop_ledger_01",
            hot_node_id="node_wf",
            candidate_mb=self.candidate_graph,
            durability_test_result={"all_passed": True},
            policy=PromotionPolicy(require_durability=True, require_shadow=False, require_human_approval=True),
            status=ProposalState.APPROVAL_READY,
        )
        runtime.pending_reorganizations["prop_ledger_01"] = prop

        mgr = AuthorityContext(
            actor_id="mgr_01",
            role="manager",
            scope="workflow",
            actor_type="human",
            authenticated_by="idp_sso",
        )
        runtime.promote_candidate_mb("prop_ledger_01", authority=mgr, use_canary=True, canary_ratio=1.0)

        # カナリアで外部への作用（回答送信）が発生
        efp = BusinessInput("T_COMP_01", "U1", "workflow", "稟議申請")
        res = runtime.dispatch_ticket(efp)
        self.assertTrue(res.is_canary)

        # 台帳にアクションが記録されている
        ledger = runtime.canary_manager.action_ledger
        self.assertEqual(len(ledger.records), 1)
        self.assertEqual(ledger.records[0].ticket_id, "T_COMP_01")
        self.assertFalse(ledger.records[0].is_compensated)

        # 破断によるロールバック発動
        runtime.rollback_active_canary(reason="誤情報提供が判明")

        # ロールバックに伴い、台帳の補償アクションが実行され compensated 状態になる
        self.assertTrue(ledger.records[0].is_compensated)
        self.assertIsNotNone(ledger.records[0].compensation_executed_at)
        self.assertIn("【システム訂正】", ledger.records[0].compensating_action["revert_notice"])


if __name__ == "__main__":
    unittest.main()

