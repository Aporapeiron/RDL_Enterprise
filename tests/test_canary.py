import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from rdl_enterprise.mb_graph import MBGraph, MBNode
from rdl_enterprise.snapshot import BusinessInput, FeedbackResult
from rdl_enterprise.authority import AuthorityContext
from rdl_enterprise.promotion_gate import ProposalState, PromotionPolicy
from rdl_enterprise.runtime import EnterpriseRuntime, ReorganizationProposal
from rdl_enterprise.canary import CanaryStatus, CanaryManager


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

        # 手動ロールバック指示
        success = runtime.rollback_active_canary(reason="業務部門からの緊急停止要請")
        self.assertTrue(success)
        self.assertIsNone(runtime.canary_manager.active_deployment)
        self.assertEqual(runtime.mb_graph.get("node_wf").action_template["payload"], "http://old-legacy.corp")


if __name__ == "__main__":
    unittest.main()
