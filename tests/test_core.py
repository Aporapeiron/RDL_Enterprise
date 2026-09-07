import unittest
import sys
import os

# src パス追加
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from rdl_enterprise.mb_graph import MBNode, MBGraph
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
        graph.add_or_update(node)
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


if __name__ == "__main__":
    unittest.main()
