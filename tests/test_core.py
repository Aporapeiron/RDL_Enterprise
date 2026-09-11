import unittest
import sys
import os

# src パス追加
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from rdl_enterprise.mb_graph import MBNode, MBGraph, CommitmentOrigin
from rdl_enterprise.h_state import HState
from rdl_enterprise.snapshot import BusinessInput, FeedbackResult
from rdl_enterprise.cascade import InterpCascade
from rdl_enterprise.human import HumanQuery
from rdl_enterprise.runtime import EnterpriseRuntime

class TestRDLCore(unittest.TestCase):
    def test_mb_node_inertia_and_bounded(self):
        node = MBNode(
            id="test_node",
            domain="test",
            trigger_pattern={"exact_keys": ["テスト"]},
            action_template={"type": "direct_reply", "payload": "OK"},
            confidence=0.5,
        )
        self.assertAlmostEqual(node.inertia(), 0.5)
        self.assertAlmostEqual(node.kappa(), 1.0, delta=0.2)

        # 成功カウント加算
        node.record_success(approved=True)
        self.assertGreater(node.inertia(), 0.5)

        # 大量失敗・差し戻しで負値にならないか（Bounded 0.0）
        node.rejection_count = 100
        self.assertEqual(node.inertia(), 0.0)
        self.assertEqual(node.kappa(), 1.0)  # exp(0) = 1.0

    def test_h_state_dissipation_and_theta_eff(self):
        h = HState(theta_0=2.0)
        self.assertAlmostEqual(h.theta_eff(), 2.0)

        # 観測残存指標を追加
        for _ in range(5):
            h.record_observation(unclassified=True, unknown_input=True)

        # 未知圧が上がると θ_eff が引き下げられる
        self.assertLess(h.theta_eff(), 2.0)

        # 熱蓄積
        h.add_heat("node_1", pred_err=1.0, input_err=0.5)
        self.assertGreater(h.node_heats["node_1"].total(), 1.0)

        # 散逸テスト
        h.dissipate({"node_1": 2.0})
        self.assertLess(h.node_heats["node_1"].total(), 1.2)

    def test_interp_cascade_and_crystallization(self):
        graph = MBGraph()
        cascade = InterpCascade(graph)

        efp = BusinessInput(
            ticket_id="T001",
            user_id="U001",
            category="network",
            query_text="Wi-Fiの繋ぎ方を教えてください",
        )

        # 初見：ルール未登録
        pred = cascade.interpret(efp)
        self.assertEqual(pred.action_type, "ask_human")

        # 人間が正解を授けて沈澱
        cascade.crystallize_rule(efp, "SSID: Corp-Secureに接続してください", "network")

        # 再度同じクエリを投入 -> Level 0 キャッシュで即答
        pred2 = cascade.interpret(efp)
        self.assertEqual(pred2.cost_tier, 0)
        self.assertEqual(pred2.action_type, "direct_reply")
        self.assertIn("Corp-Secure", pred2.content)

    def test_human_query_gate(self):
        hq = HumanQuery()
        efp = BusinessInput("T002", "U002", "security", "管理者権限をください")
        node = MBNode(
            id="admin_node",
            domain="security",
            trigger_pattern={},
            action_template={},
            authority_level="human_only",
        )
        from rdl_enterprise.snapshot import InterpretationPrediction
        pred = InterpretationPrediction("direct_reply", "OK", 0.9, "admin_node", 1)

        res = hq.evaluate(efp, pred, node)
        self.assertTrue(res["must_ask"])
        self.assertEqual(res["query_type"], "permission_request")

    def test_human_confirmation_threshold_is_single_confidence_gate(self):
        """Basic threshold changes only confidence-based confirmation for the same prediction."""
        from rdl_enterprise.snapshot import InterpretationPrediction

        efp = BusinessInput("T_HUMAN_THRESHOLD", "U002", "security", "通常の照会")
        pred = InterpretationPrediction("direct_reply", "回答", 0.55, None, 1)
        self_proceed = HumanQuery(human_confirmation_threshold=0.4).evaluate(efp, pred, None)
        confirm = HumanQuery(human_confirmation_threshold=0.7).evaluate(efp, pred, None)

        self.assertFalse(self_proceed["must_ask"])
        self.assertTrue(confirm["must_ask"])
        self.assertEqual(self_proceed["query_type"], "none")
        self.assertEqual(confirm["query_type"], "ask_guidance")

    def test_human_confirmation_threshold_does_not_bypass_mandatory_authority(self):
        """A self-proceeding confidence setting cannot disable required approval."""
        from rdl_enterprise.snapshot import InterpretationPrediction

        efp = BusinessInput("T_HUMAN_AUTHORITY", "U002", "security", "管理者権限をください")
        node = MBNode(
            id="approval_node",
            domain="security",
            trigger_pattern={},
            action_template={},
            authority_level="require_approval",
        )
        pred = InterpretationPrediction("direct_reply", "OK", 0.99, "approval_node", 1)

        result = HumanQuery(human_confirmation_threshold=0.0).evaluate(efp, pred, node)

        self.assertTrue(result["must_ask"])
        self.assertEqual(result["query_type"], "confirm_auto")

    def test_human_confirmation_threshold_preserves_unresolved_fallback(self):
        """An unresolved ask-human prediction remains a guidance request, not low confidence."""
        from rdl_enterprise.snapshot import InterpretationPrediction

        efp = BusinessInput("T_HUMAN_UNKNOWN", "U002", "security", "未知の照会")
        pred = InterpretationPrediction("ask_human", "追加情報が必要です", 0.99, None, 3)

        result = HumanQuery(human_confirmation_threshold=0.0).evaluate(efp, pred, None)

        self.assertTrue(result["must_ask"])
        self.assertEqual(result["query_type"], "ask_guidance")
        self.assertIn("未知", result["reason"])

    def test_inconsistent_complaint_escalates_for_clarification_without_auto_resolution(self):
        hq = HumanQuery()
        efp = BusinessInput(
            "T_INCONSISTENT_01", "U_COMPLAINANT", "workflow",
            "返金しろ、でもキャンセルするな、規約通りにしろ、例外にしろ",
        )
        from rdl_enterprise.snapshot import InterpretationPrediction
        pred = InterpretationPrediction(
            "ask_human",
            "要求間の優先順位を確認してください",
            0.91,
            None,
            2,
            domain="workflow",
            expected_outcome="need_input",
        )

        result = hq.evaluate(efp, pred, None)
        self.assertTrue(result["must_ask"])
        self.assertEqual(result["query_type"], "ask_guidance")
        self.assertNotEqual(result["query_type"], "none")

    def test_stress_y_raw_inconsistent_complaint_uses_safe_unknown_fallback(self):
        cascade = InterpCascade(MBGraph())
        efp = BusinessInput(
            "T_INCONSISTENT_RAW", "U_COMPLAINANT", "workflow",
            "返金しろ、でもキャンセルするな、規約通りにしろ、例外にしろ",
        )

        prediction = cascade.interpret(efp)

        self.assertEqual(prediction.action_type, "ask_human")
        self.assertEqual(prediction.expected_outcome, "escalate")
        self.assertEqual(prediction.locus_basis, "fallback")
        self.assertIsNone(prediction.matched_node_id)

    def test_async_feedback_lifecycle(self):
        from rdl_enterprise.snapshot import CaseStatus
        graph = MBGraph()
        # テスト用ノード
        node = MBNode(
            id="node_vpn",
            domain="network",
            trigger_pattern={"exact_keys": ["VPN接続"]},
            action_template={"type": "direct_reply", "payload": "VPN再起動してください"},
            confidence=0.6,
        )
        graph.commit_node(node, origin=CommitmentOrigin.TEST_FIXTURE)
        runtime = EnterpriseRuntime(mb_graph=graph)

        efp = BusinessInput("T010", "U010", "network", "VPN接続ができません")

        # 1. チケットディスパッチ（フィードバック未受領 -> PENDING）
        dispatch_res = runtime.dispatch_ticket(efp)
        self.assertEqual(dispatch_res.status, CaseStatus.PENDING)
        self.assertIn("T010", runtime.pending_snapshots)

        # 2. 翌日、ユーザーから「解決しました」とフィードバックが届く
        feedback = FeedbackResult(user_resolved=True)
        resol_res = runtime.resolve_ticket_feedback("T010", feedback)

        self.assertEqual(resol_res.status, CaseStatus.SUCCESS)
        self.assertNotIn("T010", runtime.pending_snapshots)
        self.assertEqual(len(runtime.resolved_snapshots), 1)
        self.assertEqual(node.success_count, 1)

    def test_learning_governance_validation(self):
        """先輩の助言が成功した時だけM_Bに沈澱し、失敗した時は沈澱しない検証"""
        from rdl_enterprise.snapshot import CaseStatus
        graph = MBGraph()
        runtime = EnterpriseRuntime(mb_graph=graph)

        # ケースA：先輩が教えてくれたが、結果は失敗（未解決）だった場合
        efp_fail = BusinessInput("T_FAIL", "U001", "dev", "Dockerコンテナが起動しない")
        dispatch_fail = runtime.dispatch_ticket(efp_fail, human_override_answer="docker restartを実行")
        # ディスパッチ時点では新ノードはM_Bに追加されていないはず！
        self.assertEqual(len(graph.nodes), 0)

        # 失敗フィードバック
        resol_fail = runtime.resolve_ticket_feedback("T_FAIL", FeedbackResult(user_resolved=False))
        self.assertFalse(resol_fail.promoted_to_mb)
        # 失敗したのでM_Bには追加されない！
        self.assertEqual(len(graph.nodes), 0)

        # ケースB：先輩が教えてくれて、結果も大成功した場合
        efp_succ = BusinessInput("T_SUCC", "U002", "dev", "KubernetesポッドがCrashLoop")
        dispatch_succ = runtime.dispatch_ticket(efp_succ, human_override_answer="メモリ制限を2GBへ増強")
        self.assertEqual(len(graph.nodes), 0)

        # 成功フィードバック
        resol_succ = runtime.resolve_ticket_feedback("T_SUCC", FeedbackResult(user_resolved=True))
        self.assertTrue(resol_succ.promoted_to_mb)
        # 成功したのでM_Bに正式昇格（沈澱）！
        self.assertEqual(len(graph.nodes), 1)

    def test_pending_timeout_to_unknown(self):
        """PENDING案件がタイムアウトしてUNKNOWNになり、不確実性熱が加算される検証"""
        from rdl_enterprise.snapshot import CaseStatus
        runtime = EnterpriseRuntime()
        efp = BusinessInput("T_TIMEOUT", "U003", "general", "質問です")
        runtime.dispatch_ticket(efp)
        self.assertEqual(len(runtime.pending_snapshots), 1)

        # タイムアウト実行
        results = runtime.expire_pending_tickets(["T_TIMEOUT"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, CaseStatus.UNKNOWN)
        self.assertEqual(len(runtime.pending_snapshots), 0)
        self.assertEqual(len(runtime.resolved_snapshots), 1)

        metrics = runtime.get_metrics()
        self.assertEqual(metrics["unknown_tickets_count"], 1)

    def test_authority_context(self):
        """権限コンテキストのスコープとロールの検証"""
        from rdl_enterprise.authority import AuthorityContext
        admin = AuthorityContext(actor_id="admin_01", role="admin", scope="all")
        self.assertTrue(admin.is_authorized_for("network"))
        self.assertTrue(admin.is_authorized_for("workflow"))

        network_mgr = AuthorityContext(actor_id="mgr_01", role="manager", scope="network")
        self.assertTrue(network_mgr.is_authorized_for("network"))
        self.assertFalse(network_mgr.is_authorized_for("security"))

        senior = AuthorityContext(actor_id="sen_01", role="senior", scope="all")
        self.assertFalse(senior.is_authorized_for("network"))  # seniorは公式権限者ではない

    def test_durability_harness(self):
        """破断検査ハーネス（履歴・境界）の動作検証"""
        from rdl_enterprise.durability import DurabilityHarness
        graph = MBGraph()
        # 特権ノードが誤ってautoになっている脆弱な候補M_B
        bad_node = MBNode(
            id="bad_admin",
            domain="security",
            trigger_pattern={"exact_keys": ["管理者権限申請"]},
            action_template={"type": "direct_reply", "payload": "OK"},
            authority_level="auto",  # 脆弱！
        )
        graph.commit_node(bad_node, origin=CommitmentOrigin.TEST_FIXTURE)

        harness = DurabilityHarness()
        res = harness.run_all(graph, history=[])
        self.assertFalse(res["all_passed"])  # AuthorityBoundaryCheckerで不合格になるはず！
        self.assertEqual(len(res["reports"][1]["break_points"]), 1)

    def test_m_delta_pipeline_and_promotion(self):
        """再編相 M_Δ の候補起草 -> 検査 -> 権限者承認 -> M_B置換のパイプライン検証"""
        from rdl_enterprise.authority import AuthorityContext
        graph = MBGraph()
        node = MBNode(
            id="node_wf",
            domain="workflow",
            trigger_pattern={"exact_keys": ["稟議申請"]},
            action_template={"type": "direct_reply", "payload": "http://old-url.corp"},
            confidence=0.8,
        )
        graph.commit_node(node, origin=CommitmentOrigin.TEST_FIXTURE)

        # 耐久検査合格で直接承認可能な単体テスト用ポリシー (require_shadow=False)
        from rdl_enterprise.promotion_gate import PromotionPolicy
        direct_policy = PromotionPolicy(require_durability=True, require_shadow=False, require_human_approval=True)
        runtime = EnterpriseRuntime(
            mb_graph=graph,
            theta_0=1.0,
            auto_promote_reorganizations=False,
            default_promotion_policy=direct_policy,
        )

        efp = BusinessInput("T_WF_01", "U005", "workflow", "稟議申請のURLは？")

        # 差し戻しを発生させて熱を高める (H >= theta_eff)
        runtime.handle_ticket(
            efp,
            feedback=FeedbackResult(
                user_resolved=False,
                human_rejected=True,
                new_knowledge_provided="http://new-saas.corp",
            ),
        )

        # M_Δ が発動し、プロポーザルが作成されたはず！
        self.assertEqual(len(runtime.pending_reorganizations), 1)
        prop_id = list(runtime.pending_reorganizations.keys())[0]
        prop = runtime.pending_reorganizations[prop_id]
        self.assertEqual(prop.status, "awaiting_approval")
        self.assertTrue(prop.durability_test_result["all_passed"])

        # 本番M_Bはまだ置換されていない
        self.assertEqual(runtime.mb_graph.get("node_wf").action_template["payload"], "http://old-url.corp")

        # 権限者（Manager）による正式承認！
        mgr = AuthorityContext(
            actor_id="mgr_wf",
            role="manager",
            scope="workflow",
            actor_type="human",
            authenticated_by="idp_sso",
        )
        success = runtime.promote_candidate_mb(prop_id, authority=mgr)
        self.assertTrue(success)

        # 本番M_Bが新URLへ無事昇格・置換された！
        self.assertEqual(runtime.mb_graph.get("node_wf").action_template["payload"], "http://new-saas.corp")
        self.assertEqual(len(runtime.pending_reorganizations), 0)

    def test_revision_threshold_changes_only_reinspection_boundary(self):
        """同じ更新前M_B・入力・Hで、見直し境界だけが再編開始を変える。"""
        graph = MBGraph()
        graph.commit_node(MBNode(
            id="node_revision_threshold",
            domain="workflow",
            trigger_pattern={"exact_keys": ["稟議申請"]},
            action_template={"type": "direct_reply", "payload": "old"},
        ), origin=CommitmentOrigin.TEST_FIXTURE)
        efp = BusinessInput("T_REVISION_THRESHOLD", "U1", "workflow", "稟議申請")
        feedback = FeedbackResult(user_resolved=False, human_rejected=True)

        maintain = EnterpriseRuntime(
            mb_graph=MBGraph.from_dict(graph.to_dict()),
            theta_0=2.0,
            revision_threshold=2.0,
        )
        early = EnterpriseRuntime(
            mb_graph=MBGraph.from_dict(graph.to_dict()),
            theta_0=2.0,
            revision_threshold=0.1,
        )
        maintain_result = maintain.handle_ticket(efp, feedback=feedback)
        early_result = early.handle_ticket(efp, feedback=feedback)

        self.assertEqual(maintain_result.e_prediction, early_result.e_prediction)
        self.assertEqual(maintain_result.e_input, early_result.e_input)
        self.assertAlmostEqual(maintain_result.current_h, early_result.current_h, places=6)
        self.assertFalse(maintain_result.transition_to_m_delta)
        self.assertTrue(early_result.transition_to_m_delta)
        self.assertEqual(len(maintain.pending_reorganizations), 0)
        self.assertEqual(len(early.pending_reorganizations), 1)

    def test_revision_threshold_does_not_change_raw_heat_or_canary_threshold(self):
        """Basic見直し境界はH生成とCanary安全閾値を変更しない。"""
        runtime = EnterpriseRuntime(theta_0=2.0, revision_threshold=0.1)
        self.assertEqual(runtime.h_state.theta_0, 2.0)
        self.assertIsNone(runtime.canary_manager.active_deployment)
        self.assertEqual(runtime.canary_manager.action_ledger.records, [])

    def test_revision_threshold_keeps_xi_contextual_adjustment(self):
        """明示した基底境界でもξ_obs補正と安全下限を迂回しない。"""
        h = HState(theta_0=2.0)
        self.assertAlmostEqual(h.theta_eff_for_base(2.0), 2.0)
        h.record_observation(missing_info=True)
        self.assertAlmostEqual(h.theta_eff_for_base(2.0), 1.84)
        self.assertEqual(h.theta_eff_for_base(0.1), 0.5)

    def test_knowledge_update_threshold_is_same_candidate_admission_gate(self):
        """同一Candidate・同一検査結果で、慎重度だけが採用前判定を変える。"""
        from types import SimpleNamespace

        report = {"score": 0.75, "all_passed": True}
        candidate = SimpleNamespace(durability_test_result=report)
        early = EnterpriseRuntime(knowledge_update_threshold=0.7)
        cautious = EnterpriseRuntime(knowledge_update_threshold=0.8)

        self.assertTrue(early._passes_knowledge_update_gate(candidate))
        self.assertFalse(cautious._passes_knowledge_update_gate(candidate))
        self.assertEqual(report["score"], 0.75)

    def test_knowledge_update_threshold_does_not_bypass_mandatory_durability(self):
        runtime = EnterpriseRuntime(knowledge_update_threshold=0.0)
        candidate = type("Candidate", (), {"durability_test_result": {"score": 1.0, "all_passed": False}})()
        self.assertTrue(runtime._passes_knowledge_update_gate(candidate))

    def test_delegated_authority_and_scope_limitation(self):
        """自己例外化禁止：委任権限なしでの自動昇格拒絶とスコープ限定の検証"""
        from rdl_enterprise.authority import AuthorityContext
        from rdl_enterprise.promotion_gate import PromotionPolicy

        auto_policy = PromotionPolicy(require_durability=True, require_shadow=False, require_human_approval=False)
        graph = MBGraph()
        graph.commit_node(MBNode(
            id="node_wf2",
            domain="workflow",
            trigger_pattern={"exact_keys": ["稟議申請"]},
            action_template={"type": "direct_reply", "payload": "http://old.corp"},
            confidence=0.8,
        ), origin=CommitmentOrigin.TEST_FIXTURE)

        # 1. auto_promote=True だが auto_promote_authority=None の場合 -> 自動昇格せず保留
        runtime_no_auth = EnterpriseRuntime(
            mb_graph=graph,
            theta_0=1.0,
            auto_promote_reorganizations=True,
            default_promotion_policy=auto_policy,
        )
        efp = BusinessInput("T_01", "U1", "workflow", "稟議申請")
        runtime_no_auth.handle_ticket(
            efp,
            feedback=FeedbackResult(user_resolved=False, human_rejected=True, new_knowledge_provided="http://new.corp"),
        )
        self.assertEqual(len(runtime_no_auth.pending_reorganizations), 1)
        prop_id = list(runtime_no_auth.pending_reorganizations.keys())[0]
        self.assertEqual(runtime_no_auth.pending_reorganizations[prop_id].status, "awaiting_approval")

        # 2. スコープ外の権限 (sales) が委任されている場合 -> 昇格拒絶
        sales_auth = AuthorityContext(actor_id="sales_bot", role="manager", scope="sales")
        runtime_wrong_scope = EnterpriseRuntime(
            mb_graph=graph,
            theta_0=1.0,
            auto_promote_reorganizations=True,
            auto_promote_authority=sales_auth,
            default_promotion_policy=auto_policy,
        )
        runtime_wrong_scope.handle_ticket(
            efp,
            feedback=FeedbackResult(user_resolved=False, human_rejected=True, new_knowledge_provided="http://new.corp"),
        )
        # スコープ不一致により昇格却下（本番M_Bは置換されず旧URLのまま、プロポーザルはrejectedとして履歴へ）
        self.assertEqual(len(runtime_wrong_scope.reorganization_history), 1)
        self.assertEqual(runtime_wrong_scope.reorganization_history[0].status, "rejected")
        self.assertEqual(runtime_wrong_scope.mb_graph.get("node_wf2").action_template["payload"], "http://old.corp")

        # 3. 正当なスコープの権限 (workflow) が委任されている場合 -> 正常に自動昇格
        wf_auth = AuthorityContext(actor_id="wf_delegated_admin", role="manager", scope="workflow")
        runtime_valid_auth = EnterpriseRuntime(
            mb_graph=graph,
            theta_0=1.0,
            auto_promote_reorganizations=True,
            auto_promote_authority=wf_auth,
            default_promotion_policy=auto_policy,
        )
        runtime_valid_auth.handle_ticket(
            efp,
            feedback=FeedbackResult(user_resolved=False, human_rejected=True, new_knowledge_provided="http://new.corp"),
        )
        # 昇格済み
        self.assertEqual(len(runtime_valid_auth.pending_reorganizations), 0)
        self.assertEqual(runtime_valid_auth.mb_graph.get("node_wf2").action_template["payload"], "http://new.corp")

    def test_regression_history_checker_replays_cases(self):
        """Golden Replay: 過去成功案件が改変M_B'で推論退行した場合にリグレッション検知"""
        from rdl_enterprise.cascade import InterpCascade
        from rdl_enterprise.durability import RegressionHistoryChecker
        from rdl_enterprise.snapshot import CaseSnapshot

        # 過去の成功事例を作成
        efp_pwd = BusinessInput("T_PWD", "U10", "account", "パスワードリセット")
        orig_graph = MBGraph()
        pwd_node = MBNode(
            id="node_pwd",
            domain="account",
            trigger_pattern={"exact_keys": ["パスワードリセット"]},
            action_template={"type": "direct_reply", "payload": "URL"},
            confidence=0.9,
        )
        orig_graph.commit_node(pwd_node, origin=CommitmentOrigin.TEST_FIXTURE)

        orig_cascade = InterpCascade(orig_graph)
        pred = orig_cascade.interpret(efp_pwd)
        snapshot = CaseSnapshot(efp=efp_pwd, f_pred=pred)
        snapshot.record_feedback(FeedbackResult(user_resolved=True))  # SUCCESS

        # 壊れた候補M_B'（パスワードノードが削除された、またはトリガーが変えられた）
        broken_candidate = MBGraph()
        broken_node = MBNode(
            id="node_other",
            domain="account",
            trigger_pattern={"exact_keys": ["別件"]},
            action_template={"type": "direct_reply", "payload": "URL"},
        )
        broken_candidate.commit_node(broken_node, origin=CommitmentOrigin.TEST_FIXTURE)

        checker = RegressionHistoryChecker()
        report = checker.test(broken_candidate, history=[snapshot])

        self.assertFalse(report.passed)
        self.assertGreater(len(report.break_points), 0)
        self.assertIn("node_pwd", report.break_points[0])


if __name__ == "__main__":
    unittest.main()

