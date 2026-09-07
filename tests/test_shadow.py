import unittest
from rdl_enterprise.mb_graph import MBGraph, MBNode
from rdl_enterprise.snapshot import BusinessInput, FeedbackResult
from rdl_enterprise.shadow import ShadowEvaluator
from rdl_enterprise.runtime import EnterpriseRuntime
from rdl_enterprise.authority import AuthorityContext

class TestShadowExecution(unittest.TestCase):

    def setUp(self):
        # 現行本番 M_B (旧URL)
        self.prod_graph = MBGraph()
        self.prod_graph.add_or_update(MBNode(
            id="node_wf",
            domain="workflow",
            trigger_pattern={"exact_keys": ["稟議申請"]},
            action_template={"type": "direct_reply", "payload": "http://old-legacy.corp"},
            confidence=0.8,
        ))

        # 候補 M_B' (新SaaS URL)
        self.candidate_graph = MBGraph()
        self.candidate_graph.add_or_update(MBNode(
            id="node_wf",
            domain="workflow",
            trigger_pattern={"exact_keys": ["稟議申請"]},
            action_template={"type": "direct_reply", "payload": "https://new-saas.corp"},
            confidence=0.85,
        ))

    def test_shadow_prediction_and_triplet_comparison(self):
        """本番とシャドウの並行推論、および実結果フィードバック時の三者比較を検証"""
        evaluator = ShadowEvaluator(
            proposal_id="prop_test_01",
            prod_mb=self.prod_graph,
            candidate_mb=self.candidate_graph,
        )

        efp = BusinessInput(
            ticket_id="TICK-SHADOW-01",
            user_id="user_100",
            category="workflow",
            query_text="稟議申請の方法を教えて",
        )

        # 1. シャドウ推論 (並行実行)
        pair = evaluator.evaluate_input(efp)
        self.assertEqual(pair.prod_pred.content, "http://old-legacy.corp")
        self.assertEqual(pair.shadow_pred.content, "https://new-saas.corp")
        self.assertTrue(pair.content_changed)

        # 2. 事後結果受領 (三者比較: 旧予測 vs 新予測 vs 実結果)
        # ユーザー:「新SaaSポータル(https://new-saas.corp)からじゃないと申請できない！」
        feedback = FeedbackResult(
            user_resolved=False,
            human_rejected=True,
            new_knowledge_provided="https://new-saas.corp",
        )
        triplet = evaluator.record_feedback("TICK-SHADOW-01", feedback)
        self.assertIsNotNone(triplet)
        self.assertEqual(triplet.prod_pred_error, 1.0)
        self.assertEqual(triplet.shadow_pred_error, 0.0)
        self.assertTrue(triplet.is_improved)
        self.assertFalse(triplet.is_regressed)

        # 3. 集計レポート
        report = evaluator.generate_report()
        self.assertEqual(report.total_shadow_cases, 1)
        self.assertEqual(report.improved_count, 1)
        self.assertEqual(report.regressed_count, 0)
        self.assertEqual(report.regression_rate, 0.0)
        self.assertTrue(report.passed)

    def test_runtime_shadow_mode_integration(self):
        """EnterpriseRuntime に統合されたシャドウモードのライフサイクル検証"""
        runtime = EnterpriseRuntime(
            mb_graph=self.prod_graph,
            theta_0=1.0,
            auto_promote_reorganizations=False,
        )

        efp_wf = BusinessInput("TICK-INIT", "U01", "workflow", "稟議申請")
        # 差し戻しで M_Δ 発動
        runtime.handle_ticket(
            efp_wf,
            feedback=FeedbackResult(
                user_resolved=False,
                human_rejected=True,
                new_knowledge_provided="https://new-saas.corp",
            ),
        )

        self.assertEqual(len(runtime.pending_reorganizations), 1)
        prop_id = list(runtime.pending_reorganizations.keys())[0]

        # シャドウモードを起動！
        success = runtime.enable_shadow_mode(prop_id)
        self.assertTrue(success)
        self.assertIsNotNone(runtime.active_shadow_evaluator)

        # その後のチケットを通常通り dispatch & resolve
        efp_after = BusinessInput("TICK-AFTER-01", "U02", "workflow", "稟議申請")
        runtime.dispatch_ticket(efp_after)

        runtime.resolve_ticket_feedback(
            "TICK-AFTER-01",
            FeedbackResult(
                user_resolved=False,
                human_rejected=True,
                new_knowledge_provided="https://new-saas.corp",
            ),
        )

        # シャドウレポートを確認
        report = runtime.get_shadow_report()
        self.assertIsNotNone(report)
        self.assertEqual(report.resolved_triplets_count, 1)
        self.assertEqual(report.improved_count, 1)

        # 正式承認・本番置換 (Leap) 実行 -> シャドウモードが自動終了すること
        admin = AuthorityContext(actor_id="admin_01", role="manager", scope="workflow")
        runtime.promote_candidate_mb(prop_id, authority=admin)
        self.assertIsNone(runtime.active_shadow_evaluator)
        self.assertEqual(runtime.mb_graph.get("node_wf").action_template["payload"], "https://new-saas.corp")

if __name__ == "__main__":
    unittest.main()