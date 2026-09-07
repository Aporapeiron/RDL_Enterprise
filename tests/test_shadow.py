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

    def test_shadow_prediction_and_counterfactual_triplet(self):
        """本番予測(実績)と候補推論(シャドウ)、および実結果フィードバック時の反実仮想三者比較を検証"""
        evaluator = ShadowEvaluator(
            proposal_id="prop_test_01",
            prod_mb=self.prod_graph,
            candidate_mb=self.candidate_graph,
            minimum_resolved_cases=1,
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

        # 2. 事後結果受領 (観測誤差 E_prod vs 反実仮想推定 E_shadow)
        # ユーザー:「旧URLは繋がらない！新SaaS(https://new-saas.corp)が正解」
        feedback = FeedbackResult(
            user_resolved=False,
            human_rejected=True,
            new_knowledge_provided="https://new-saas.corp",
        )
        triplet = evaluator.record_feedback("TICK-SHADOW-01", feedback)
        self.assertIsNotNone(triplet)
        self.assertEqual(triplet.prod_observed_error, 1.0)                      # 観測された事実
        self.assertEqual(triplet.shadow_counterfactual_error_estimate, 0.0)    # 反実仮想の推定
        self.assertTrue(triplet.is_improved)
        self.assertFalse(triplet.is_regressed)

        # 3. 集計レポート
        report = evaluator.generate_report()
        self.assertEqual(report.total_shadow_cases, 1)
        self.assertEqual(report.improved_count, 1)
        self.assertEqual(report.regressed_count, 0)
        self.assertEqual(report.regression_rate, 0.0)
        self.assertEqual(report.evaluation_status, "passed")
        self.assertTrue(report.passed)

    def test_insufficient_evidence_when_zero_cases_resolved(self):
        """非終端閉包性: 解決案件が0件の場合は passed=True ではなく insufficient_evidence (不合格) とする"""
        evaluator = ShadowEvaluator(
            proposal_id="prop_empty_01",
            prod_mb=self.prod_graph,
            candidate_mb=self.candidate_graph,
            minimum_resolved_cases=1,
        )

        # 1件も解決していない段階
        report = evaluator.generate_report()
        self.assertEqual(report.evaluation_status, "insufficient_evidence")
        self.assertFalse(report.passed)  # 証拠不十分なので昇格不可！

    def test_runtime_shadow_mode_integration_and_prod_pred_injection(self):
        """EnterpriseRuntime で本番予測が二重推論されずに注入され、三者比較が回ることを検証"""
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

        prop_id = list(runtime.pending_reorganizations.keys())[0]

        # シャドウモードを起動！(最小必要件数: 1)
        success = runtime.enable_shadow_mode(prop_id, minimum_resolved_cases=1)
        self.assertTrue(success)

        # チケットを dispatch & resolve
        efp_after = BusinessInput("TICK-AFTER-01", "U02", "workflow", "稟議申請")
        dispatch_res = runtime.dispatch_ticket(efp_after)

        # dispatch時に本番予測が正しくシャドウ側に記録されていること
        pair = runtime.active_shadow_evaluator.pending_pairs.get("TICK-AFTER-01")
        self.assertIsNotNone(pair)
        self.assertEqual(pair.prod_pred.content, dispatch_res.final_output)

        # フィードバック受領
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
        self.assertEqual(report.evaluation_status, "passed")
        self.assertTrue(report.passed)
        self.assertEqual(report.improved_count, 1)

        # 正式承認・本番置換 (Leap)
        admin = AuthorityContext(
            actor_id="admin_01",
            role="manager",
            scope="workflow",
            actor_type="human",
            authenticated_by="idp_sso",
        )
        runtime.promote_candidate_mb(prop_id, authority=admin)
        self.assertIsNone(runtime.active_shadow_evaluator)
        self.assertEqual(runtime.mb_graph.get("node_wf").action_template["payload"], "https://new-saas.corp")

    def test_shadow_diversity_and_coverage_metrics(self):
        """ShadowEvaluator がユニーククエリ数、カバーカテゴリ、多様性スコアを正しく集計することを検証"""
        evaluator = ShadowEvaluator(
            proposal_id="prop_diversity_01",
            prod_mb=self.prod_graph,
            candidate_mb=self.candidate_graph,
            minimum_resolved_cases=2,
        )

        # 3件の入力（2件は同一クエリ、1件は別クエリ。カテゴリは2種類）
        efp1 = BusinessInput("T1", "U1", "workflow", "稟議申請のURL")
        efp2 = BusinessInput("T2", "U2", "workflow", " 稟議申請のURL ")  # 正規化で efp1 と同一とみなされる
        efp3 = BusinessInput("T3", "U3", "finance", "経費精算の手順")

        evaluator.evaluate_input(efp1)
        evaluator.evaluate_input(efp2)
        evaluator.evaluate_input(efp3)

        evaluator.record_feedback("T1", FeedbackResult(user_resolved=True))
        evaluator.record_feedback("T2", FeedbackResult(user_resolved=True))
        evaluator.record_feedback("T3", FeedbackResult(user_resolved=True))

        report = evaluator.generate_report()
        self.assertEqual(report.resolved_triplets_count, 3)
        # ユニーククエリは2種類 ("稟議申請のurl", "経費精算の手順")
        self.assertEqual(report.unique_queries_count, 2)
        # カバーされたカテゴリは ["finance", "workflow"]
        self.assertEqual(report.covered_categories, ["finance", "workflow"])
        # 多様性スコア = 2 / 3
        self.assertAlmostEqual(report.diversity_score, 2 / 3, places=2)

    def test_symbol_noise_does_not_inflate_diversity_score(self):
        """記号や句読点の差異（?、!、空白等）で多様性スコアが水増しされないことを検証"""
        evaluator = ShadowEvaluator(
            proposal_id="prop_noise_01",
            prod_mb=self.prod_graph,
            candidate_mb=self.candidate_graph,
            minimum_resolved_cases=3,
        )

        # 表面上の文字列は異なるが、意図は完全に同一の3件
        efp1 = BusinessInput("T1", "U1", "workflow", "稟議申請の方法")
        efp2 = BusinessInput("T2", "U2", "workflow", "稟議申請の方法？")
        efp3 = BusinessInput("T3", "U3", "workflow", " 稟議申請の方法！！！ ")

        evaluator.evaluate_input(efp1)
        evaluator.evaluate_input(efp2)
        evaluator.evaluate_input(efp3)

        evaluator.record_feedback("T1", FeedbackResult(user_resolved=True))
        evaluator.record_feedback("T2", FeedbackResult(user_resolved=True))
        evaluator.record_feedback("T3", FeedbackResult(user_resolved=True))

        report = evaluator.generate_report()
        self.assertEqual(report.resolved_triplets_count, 3)
        # 記号ノイズが除去され、ユニークインテントは 1 種類！
        self.assertEqual(report.unique_queries_count, 1)
        self.assertEqual(report.unique_patterns_count, 1)
        # 多様性スコアは 1/3 (0.33)
        self.assertAlmostEqual(report.diversity_score, 1 / 3, places=2)


if __name__ == "__main__":
    unittest.main()