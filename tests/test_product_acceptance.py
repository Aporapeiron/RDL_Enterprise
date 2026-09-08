import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from rdl_enterprise.mb_graph import MBGraph, MBNode
from rdl_enterprise.snapshot import BusinessInput, FeedbackResult, CaseStatus
from rdl_enterprise.authority import AuthorityContext
from rdl_enterprise.promotion_gate import ProposalState, PromotionPolicy
from rdl_enterprise.runtime import EnterpriseRuntime, ReorganizationProposal


class TestProductAcceptanceMetabolicLoop(unittest.TestCase):
    """
    RDL Enterprise 製品受入テスト: 最小代謝閉ループ実証
    「経験する -> 成功確認 -> 判断が M_B に沈澱 -> 次回推論が Tier 0 へ軽量化」
    """

    def setUp(self):
        self.prod_graph = MBGraph(version="v1.0")
        self.prod_graph.add_or_update(MBNode(
            id="node_wf_ringi",
            domain="workflow",
            trigger_pattern={"exact_keys": ["稟議申請の方法"]},
            action_template={"type": "direct_reply", "payload": "社内ワークフローポータルから申請してください"},
            confidence=0.8,
        ))

        self.candidate_graph = MBGraph(version="v2.0-cand")
        self.candidate_graph.add_or_update(MBNode(
            id="node_wf_ringi",
            domain="workflow",
            trigger_pattern={"exact_keys": ["稟議申請の方法"]},
            action_template={"type": "direct_reply", "payload": "新SaaSワークフローポータルから申請してください"},
            confidence=0.9,
        ))

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
        norm_key = ("workflow", runtime.cascade._normalize("稟議申請の方法"))
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
        norm_key = ("workflow", runtime.cascade._normalize("稟議申請の方法"))

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
        norm_key = ("workflow", runtime.cascade._normalize("稟議申請の方法"))
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

        norm_key = ("workflow", runtime.cascade._normalize("稟議申請の方法"))
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


if __name__ == "__main__":
    unittest.main()
