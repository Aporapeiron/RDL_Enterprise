import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from rdl_enterprise.mb_graph import MBGraph, MBNode
from rdl_enterprise.snapshot import BusinessInput, FeedbackResult
from rdl_enterprise.authority import AuthorityContext
from rdl_enterprise.promotion_gate import ProposalState, PromotionPolicy, PromotionGate
from rdl_enterprise.runtime import EnterpriseRuntime, ReorganizationProposal
from rdl_enterprise.canary import (
    CanaryStatus,
    CanaryManager,
    CanaryCompletionPolicy,
    ActionLedger,
    ActionRecord,
    CompensationExecutor,
)


class TestCandidateImmutabilityAndBinding(unittest.TestCase):

    def setUp(self):
        self.prod_graph = MBGraph(version="v1.0")
        self.prod_graph.add_or_update(MBNode(
            id="node_wf",
            domain="workflow",
            trigger_pattern={"exact_keys": ["稟議申請"]},
            action_template={"type": "direct_reply", "payload": "http://old-legacy.corp"},
            confidence=0.8,
        ))

        self.candidate_graph = MBGraph(version="v2.0-cand-001")
        self.candidate_graph.add_or_update(MBNode(
            id="node_wf",
            domain="workflow",
            trigger_pattern={"exact_keys": ["稟議申請"]},
            action_template={"type": "direct_reply", "payload": "https://new-saas.corp"},
            confidence=0.9,
        ))

    def test_candidate_freeze_prevents_direct_modification(self):
        """候補グラフの freeze() により、ノード追加・削除が RuntimeError で拒絶されること"""
        self.candidate_graph.freeze()
        self.assertTrue(self.candidate_graph.is_frozen)

        # 変更試行はブロックされる
        with self.assertRaises(RuntimeError):
            self.candidate_graph.add_or_update(MBNode(
                id="illegal_node",
                domain="workflow",
                trigger_pattern={"exact_keys": ["不正追加"]},
                action_template={"type": "direct_reply", "payload": "fail"},
            ))

        with self.assertRaises(RuntimeError):
            self.candidate_graph.remove("node_wf")

    def test_promotion_gate_rejects_tampered_candidate_hash_binding(self):
        """Hash Binding: 検査後に候補グラフが改変された場合、PromotionGate が即座に拒絶(REJECTED)すること"""
        # 1. 検査時のハッシュ
        durability_result = {
            "all_passed": True,
            "overall_score": 1.0,
            "candidate_version": self.candidate_graph.version,
            "candidate_content_hash": self.candidate_graph.content_hash(),
        }
        policy = PromotionPolicy(require_durability=True, require_shadow=False, require_human_approval=True)

        # 正常時は通過
        res_ok = PromotionGate.evaluate_readiness(
            current_state=ProposalState.DRAFT,
            durability_result=durability_result,
            shadow_report=None,
            policy=policy,
            candidate_mb=self.candidate_graph,
        )
        self.assertTrue(res_ok.can_promote)

        # 2. 検査後に候補グラフの action_template が改変された（Identity Drift）
        tampered_graph = MBGraph.from_dict(self.candidate_graph.to_dict())
        tampered_graph.get("node_wf").action_template["payload"] = "https://malicious-site.corp"

        # ゲート判定時にハッシュ不一致を検知して REJECTED
        res_fail = PromotionGate.evaluate_readiness(
            current_state=ProposalState.DRAFT,
            durability_result=durability_result,
            shadow_report=None,
            policy=policy,
            candidate_mb=tampered_graph,
        )
        self.assertFalse(res_fail.can_promote)
        self.assertEqual(res_fail.next_state, ProposalState.REJECTED)
        self.assertIn("Identity Drift", res_fail.reasons[0])

    def test_action_ledger_scoped_to_deployment_id(self):
        """ActionLedger: ロールバック時の補償実行が該当 deployment_id のみに限定されること"""
        ledger = ActionLedger()

        # セッション1 (dep_01)
        ledger.record_action(
            ticket_id="T_01",
            mb_version="v2.0-cand-01",
            is_canary=True,
            action_type="direct_reply",
            payload="回答1",
            deployment_id="dep_01",
            proposal_id="prop_01",
            compensating_action={"type": "revert", "notice": "取消1"},
        )

        # セッション2 (dep_02)
        ledger.record_action(
            ticket_id="T_02",
            mb_version="v2.0-cand-02",
            is_canary=True,
            action_type="direct_reply",
            payload="回答2",
            deployment_id="dep_02",
            proposal_id="prop_02",
            compensating_action={"type": "revert", "notice": "取消2"},
        )

        # dep_02 のみがロールバックされた場合
        comp_results = ledger.compensate_canary_actions("dep_02")

        self.assertEqual(len(comp_results), 1)
        self.assertEqual(comp_results[0]["ticket_id"], "T_02")

        # dep_01 のアクションは未補償 (executed) のまま無傷であること
        self.assertEqual(ledger.records[0].ticket_id, "T_01")
        self.assertEqual(ledger.records[0].status, "executed")
        self.assertFalse(ledger.records[0].is_compensated)

        # dep_02 のアクションのみが補償されたこと
        self.assertEqual(ledger.records[1].ticket_id, "T_02")
        self.assertTrue(ledger.records[1].is_compensated)

    def test_canary_completion_gate_is_mandatory_by_default(self):
        """Completion Gate 必須化: policy 引数を渡さなくても 0 件コミットが確実に拒絶されること"""
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph, theta_0=1.0)
        prop = ReorganizationProposal(
            proposal_id="prop_mand_01",
            hot_node_id="node_wf",
            candidate_mb=self.candidate_graph,
            durability_test_result={"all_passed": True, "candidate_content_hash": self.candidate_graph.content_hash()},
            policy=PromotionPolicy(require_durability=True, require_shadow=False, require_human_approval=True),
            status=ProposalState.APPROVAL_READY,
        )
        runtime.pending_reorganizations["prop_mand_01"] = prop

        mgr = AuthorityContext(
            actor_id="mgr_01",
            role="manager",
            scope="workflow",
            actor_type="human",
            authenticated_by="idp_sso",
        )
        runtime.promote_candidate_mb("prop_mand_01", authority=mgr, use_canary=True, canary_ratio=1.0)

        # policy を渡さずに即座に complete_canary_rollout() を呼び出し
        # デフォルトで CanaryCompletionPolicy(minimum_cases=1, minimum_successes=1) が強制され、0件なので拒絶される
        success = runtime.complete_canary_rollout()
        self.assertFalse(success)
        self.assertIsNotNone(runtime.canary_manager.active_deployment)

        # 1件正常解決
        efp = BusinessInput("T_MAND_01", "U1", "workflow", "稟議申請の承認手続き")
        runtime.dispatch_ticket(efp)
        runtime.resolve_ticket_feedback("T_MAND_01", FeedbackResult(user_resolved=True))

        # 1件解決後はデフォルトポリシーを満たしてコミット成功
        success2 = runtime.complete_canary_rollout()
        self.assertTrue(success2)
        self.assertIsNone(runtime.canary_manager.active_deployment)


    def test_deep_node_freeze_blocks_record_success_and_failure(self):
        """Deep Freeze: 凍結された MBNode の record_success / record_failure は RuntimeError で拒絶されること"""
        self.candidate_graph.freeze()
        node = self.candidate_graph.get("node_wf")
        self.assertTrue(node.is_frozen)

        with self.assertRaises(RuntimeError):
            node.record_success()

        with self.assertRaises(RuntimeError):
            node.record_failure()

    def test_content_hash_includes_dynamical_inertia_parameters(self):
        """全行動状態ハッシュ化: success_count や m0 など慣性質量・自己修正可能性に効く値が異なればハッシュが変化すること"""
        base_hash = self.candidate_graph.content_hash()

        # success_count の改変
        tampered1 = MBGraph.from_dict(self.candidate_graph.to_dict())
        tampered1.get("node_wf").success_count += 5
        self.assertNotEqual(tampered1.content_hash(), base_hash)

        # m0 の改変
        tampered2 = MBGraph.from_dict(self.candidate_graph.to_dict())
        tampered2.m0 = 5.0
        self.assertNotEqual(tampered2.content_hash(), base_hash)

    def test_canary_observations_do_not_pollute_prod_xi_obs_or_theta_eff(self):
        """Version-aware 観測統計: カナリアで差し戻しが多発しても、本番の xi_obs および theta_eff が一切変動しないこと"""
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph, theta_0=2.0)
        initial_prod_xi = runtime.h_state.xi_obs("prod")
        initial_prod_theta = runtime.h_state.theta_eff("prod")

        prop = ReorganizationProposal(
            proposal_id="prop_obs_01",
            hot_node_id="node_wf",
            candidate_mb=self.candidate_graph,
            durability_test_result={"all_passed": True, "candidate_content_hash": self.candidate_graph.content_hash()},
            policy=PromotionPolicy(require_durability=True, require_shadow=False, require_human_approval=True),
            status=ProposalState.APPROVAL_READY,
        )
        runtime.pending_reorganizations["prop_obs_01"] = prop

        mgr = AuthorityContext(actor_id="mgr_01", role="manager", scope="workflow", actor_type="human", authenticated_by="idp_sso")
        runtime.promote_candidate_mb("prop_obs_01", authority=mgr, use_canary=True, canary_ratio=1.0, theta_canary=100.0, max_canary_failures=10)

        # カナリアで差し戻し案件を複数処理
        for i in range(3):
            tid = f"T_CANARY_REJ_{i}"
            efp = BusinessInput(tid, "U1", "workflow", "稟議申請の承認手続き")
            runtime.dispatch_ticket(efp)
            runtime.resolve_ticket_feedback(tid, FeedbackResult(user_resolved=False, human_rejected=True))

        # 1. 本番の観測統計・xi_obs・theta_eff が完全に不変であること（混入ゼロ）
        self.assertEqual(runtime.h_state.xi_obs("prod"), initial_prod_xi)
        self.assertEqual(runtime.h_state.theta_eff("prod"), initial_prod_theta)
        self.assertEqual(runtime.h_state.total_tickets, 0)
        self.assertEqual(runtime.h_state.rejection_events_count, 0)

        # 2. カナリア版 (v2.0-cand-001) の観測統計には差し戻しが記録されていること
        cand_ver = self.candidate_graph.version
        self.assertGreater(runtime.h_state.xi_obs(cand_ver), 0.0)

    def test_compensation_states_and_executor_results(self):
        """World Rollback: 補償実行の成否が厳格に記録され、succeeded の場合のみ is_compensated が True となること"""
        ledger = ActionLedger()

        # 成功する補償アクション
        rec1 = ledger.record_action(
            ticket_id="T_SUCC_01",
            mb_version="v2.0",
            is_canary=True,
            action_type="direct_reply",
            payload="回答",
            deployment_id="dep_test",
            compensating_action={"type": "send_correction"},
        )

        # 失敗をシミュレートする Executor
        class MockFailingExecutor(CompensationExecutor):
            def execute_compensation(self, action_record: ActionRecord):
                return {"success": False, "reason": "外部システム接続エラー"}

        failing_ledger = ActionLedger(default_executor=MockFailingExecutor())
        rec2 = failing_ledger.record_action(
            ticket_id="T_FAIL_01",
            mb_version="v2.0",
            is_canary=True,
            action_type="direct_reply",
            payload="回答",
            deployment_id="dep_fail",
            compensating_action={"type": "send_correction"},
        )

        # 成功ケースの補償実行
        ledger.compensate_canary_actions("dep_test")
        self.assertEqual(rec1.status, "succeeded")
        self.assertTrue(rec1.is_compensated)

        # 失敗ケースの補償実行
        failing_ledger.compensate_canary_actions("dep_fail")
        self.assertEqual(rec2.status, "failed")
        self.assertFalse(rec2.is_compensated)  # 失敗時は is_compensated が False になる！


if __name__ == "__main__":
    unittest.main()
