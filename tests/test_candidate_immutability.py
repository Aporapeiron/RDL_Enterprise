import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from rdl_enterprise.mb_graph import MBGraph, MBNode
from rdl_enterprise.snapshot import BusinessInput, FeedbackResult, SubsequentInterpretation, CaseStatus
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
    ActionCapability,
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
        ledger.executor.register_handler("revert", lambda rec: {"success": True, "reverted": True})

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
        ledger.executor.register_handler("send_correction", lambda rec: {"success": True, "corrected": True})

        # 成功する補償アクション (ハンドラ登録済み)
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

        # 3. ハンドラ未登録の補償アクション (fail-closed 検証)
        unregistered_ledger = ActionLedger()
        rec3 = unregistered_ledger.record_action(
            ticket_id="T_UNREG_01",
            mb_version="v2.0",
            is_canary=True,
            action_type="direct_reply",
            payload="回答",
            deployment_id="dep_unreg",
            compensating_action={"type": "unregistered_action_type"},
        )

        # 成功ケースの補償実行
        ledger.compensate_canary_actions("dep_test")
        self.assertEqual(rec1.status, "succeeded")
        self.assertTrue(rec1.is_compensated)

        # 失敗ケースの補償実行
        failing_ledger.compensate_canary_actions("dep_fail")
        self.assertEqual(rec2.status, "failed")
        self.assertFalse(rec2.is_compensated)  # 失敗時は is_compensated が False になる！

        # 未登録ケース (fail-closed) の補償実行
        unregistered_ledger.compensate_canary_actions("dep_unreg")
        self.assertEqual(rec3.status, "failed")
        self.assertFalse(rec3.is_compensated)
        self.assertIn("補償ハンドラ未登録 (fail-closed", rec3.compensation_result.get("reason", ""))

    def test_true_deep_freeze_blocks_attribute_and_dict_mutations(self):
        """真のDeep Freeze: 凍結ノードの属性直接代入、および内部辞書・リストの変更試行が例外で阻止されること"""
        self.candidate_graph.freeze()
        node = self.candidate_graph.get("node_wf")

        # 1. 属性直接代入のブロック (RuntimeError)
        with self.assertRaises(RuntimeError):
            node.confidence = 0.99

        # 2. 内部辞書(action_template)のキー変更ブロック (TypeError)
        with self.assertRaises(TypeError):
            node.action_template["payload"] = "https://hacked.corp"

        # 3. 内部リスト(trigger_pattern.exact_keys)の変更ブロック (TypeError)
        with self.assertRaises(TypeError):
            node.trigger_pattern["exact_keys"].append("悪意のあるキー")

        # 4. 凍結解除後は正常に変更可能であること
        self.candidate_graph.unfreeze()
        node.confidence = 0.95
        self.assertEqual(node.confidence, 0.95)
        node.action_template["payload"] = "https://normal.corp"
        self.assertEqual(node.action_template["payload"], "https://normal.corp")

    def test_canary_feedback_does_not_trigger_prod_m_delta_when_prod_near_threshold(self):
        """Canary M_Δ 完全分離: 本番熱が閾値近傍(H_prod >= θ)でも、Canary案件の処理で本番M_Δが発火しないこと"""
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph, theta_0=1.0)
        # 本番ノードに限界近傍の熱を注入
        runtime.h_state.add_heat("node_wf", pred_err=1.5, input_err=0.5, mb_version="prod", is_canary=False)
        self.assertGreaterEqual(runtime.h_state.node_heats["node_wf"].total(), runtime.h_state.theta_eff("prod"))

        # カナリア展開開始
        prop = ReorganizationProposal(
            proposal_id="prop_iso_01",
            hot_node_id="node_wf",
            candidate_mb=self.candidate_graph,
            durability_test_result={"all_passed": True, "candidate_content_hash": self.candidate_graph.content_hash()},
            policy=PromotionPolicy(require_durability=True, require_shadow=False, require_human_approval=True),
            status=ProposalState.APPROVAL_READY,
        )
        runtime.pending_reorganizations["prop_iso_01"] = prop
        mgr = AuthorityContext(actor_id="mgr_01", role="manager", scope="workflow", actor_type="human", authenticated_by="idp_sso")
        runtime.promote_candidate_mb("prop_iso_01", authority=mgr, use_canary=True, canary_ratio=1.0)

        # カナリア案件をディスパッチ＆フィードバック処理
        efp = BusinessInput("T_CAN_ISO", "U1", "workflow", "稟議申請の承認手続き")
        runtime.dispatch_ticket(efp)
        res = runtime.resolve_ticket_feedback("T_CAN_ISO", FeedbackResult(user_resolved=True))

        # 本番側の M_Δ は発火せず、プロポーザルも生成されないこと
        self.assertFalse(res.transition_to_m_delta)
        self.assertEqual(runtime.m_delta_count, 0)
        self.assertNotIn("prop_iso_01", runtime.reorganization_history)

    def test_canary_heat_and_observations_inherited_to_prod_on_full_commit(self):
        """代謝の連続性 (公理B4): カナリア展開完了(Full Commit)時に、カナリア中の残存熱・観測統計が新本番へ継承されること"""
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph, theta_0=2.0)
        prop = ReorganizationProposal(
            proposal_id="prop_inherit_01",
            hot_node_id="node_wf",
            candidate_mb=self.candidate_graph,
            durability_test_result={"all_passed": True, "candidate_content_hash": self.candidate_graph.content_hash()},
            policy=PromotionPolicy(require_durability=True, require_shadow=False, require_human_approval=True),
            status=ProposalState.APPROVAL_READY,
        )
        runtime.pending_reorganizations["prop_inherit_01"] = prop
        mgr = AuthorityContext(actor_id="mgr_01", role="manager", scope="workflow", actor_type="human", authenticated_by="idp_sso")
        runtime.promote_candidate_mb("prop_inherit_01", authority=mgr, use_canary=True, canary_ratio=1.0)

        # カナリア期間中に軽微な不整合(E=0.2)と未知入力を伴う案件を処理
        efp = BusinessInput("T_CAN_INH", "U1", "workflow", "稟議申請の承認手続き")
        runtime.dispatch_ticket(efp)
        runtime.resolve_ticket_feedback("T_CAN_INH", FeedbackResult(user_resolved=True, human_approved=False))

        # コミット前の本番統計は空
        self.assertEqual(runtime.h_state.total_tickets, 0)

        # 全面展開完了 (Full Commit)
        success = runtime.complete_canary_rollout()
        self.assertTrue(success)

        # 新本番にカナリアでのチケット数、観測統計、および残存熱が継承されていること
        self.assertEqual(runtime.h_state.total_tickets, 1)
        self.assertIn("node_wf", runtime.h_state.node_heats)
        # カナリア版バケットはクリーンアップされていること
        cand_ver = self.candidate_graph.version
        self.assertNotIn(cand_ver, runtime.h_state.versioned_observations)

    def test_action_capability_and_compensation_executor_handlers(self):
        """ActionCapability & CompensationExecutor: 可逆性分類と登録ハンドラによる補償実行"""
        executor = CompensationExecutor()
        executed_custom_undos = []

        def custom_undo_handler(action_record: ActionRecord):
            executed_custom_undos.append(action_record.action_id)
            return {"success": True, "undone": True, "target": action_record.ticket_id}

        executor.register_handler("custom_undo", custom_undo_handler)
        ledger = ActionLedger(default_executor=executor)

        # 1. COMPENSATABLE (カスタムハンドラ登録済み)
        rec_comp = ledger.record_action(
            ticket_id="T_CAP_01",
            mb_version="v2.0",
            is_canary=True,
            action_type="tool_call",
            payload={"cmd": "send_msg"},
            deployment_id="dep_cap",
            capability=ActionCapability.COMPENSATABLE,
            compensating_action={"type": "custom_undo"},
        )

        # 2. IRREVERSIBLE (不可逆アクション)
        rec_irrev = ledger.record_action(
            ticket_id="T_CAP_02",
            mb_version="v2.0",
            is_canary=True,
            action_type="payment",
            payload={"amount": 1000},
            deployment_id="dep_cap",
            capability=ActionCapability.IRREVERSIBLE,
        )

        # 3. DRY_RUN_ONLY (副作用なし)
        rec_dry = ledger.record_action(
            ticket_id="T_CAP_03",
            mb_version="v2.0",
            is_canary=True,
            action_type="read_only",
            payload={"query": "status"},
            deployment_id="dep_cap",
            capability=ActionCapability.DRY_RUN_ONLY,
        )

        # ロールバック補償実行
        results = ledger.compensate_canary_actions("dep_cap")

        # COMPENSATABLE: カスタムハンドラが呼ばれて succeeded
        self.assertEqual(rec_comp.status, "succeeded")
        self.assertTrue(rec_comp.is_compensated)
        self.assertIn(rec_comp.action_id, executed_custom_undos)

        # IRREVERSIBLE: uncompensated_irreversible になり is_compensated は False
        self.assertEqual(rec_irrev.status, "uncompensated_irreversible")
        self.assertFalse(rec_irrev.is_compensated)

        # DRY_RUN_ONLY: 補償不要で succeeded
        self.assertEqual(rec_dry.status, "succeeded")
        self.assertTrue(rec_dry.is_compensated)

    def test_t0_spec_subsequent_interpretation_f_prime_derivation(self):
        """T0 SPEC: 更新前の同一構造前提から後続解釈 F' が導出され、Δ(F, F') から E が算出されること"""
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph, theta_0=2.0)
        efp = BusinessInput("T_SPEC_01", "U1", "workflow", "稟議申請の承認")

        # 1. チケットディスパッチ (EFP -> F)
        d_res = runtime.dispatch_ticket(efp)
        snapshot = runtime.pending_snapshots["T_SPEC_01"]

        # 更新前前提 (frozen_node_snapshot) が保持されていること
        self.assertIsNotNone(snapshot.frozen_node_snapshot)
        self.assertEqual(snapshot.frozen_node_snapshot.id, "node_wf")
        self.assertIsNone(snapshot.f_prime)

        # 2. 事後結果 EFP' 受領 (差し戻し発生)
        feedback = FeedbackResult(user_resolved=False, human_rejected=True)
        r_res = runtime.resolve_ticket_feedback("T_SPEC_01", feedback)

        # 3. 後続作用解釈 F' (SubsequentInterpretation) が更新前構造から正しく導出されていること
        self.assertIsNotNone(snapshot.f_prime)
        self.assertIsInstance(snapshot.f_prime, SubsequentInterpretation)
        self.assertEqual(snapshot.f_prime.actual_status, CaseStatus.REJECTED)
        self.assertEqual(snapshot.f_prime.actual_outcome, "rejected")
        self.assertEqual(snapshot.f_prime.confidence_prime, 0.0)
        self.assertIn("更新前モデルの解釈境界が破断", snapshot.f_prime.explanation)

        # 4. F と F' の差分 Δ(F, F') からの誤差 E 算出が一致していること
        self.assertEqual(snapshot.e_prediction, snapshot.f_prime.e_prediction_delta)
        self.assertGreaterEqual(r_res.e_prediction, 1.5)

    def test_runtime_wires_action_capability_from_mbnode_definition(self):
        """ActionCapability 作用定義貫通: MBNode の action_template 定義が Runtime を経て Ledger に正確に伝播すること"""
        graph = MBGraph(version="v1.0")
        graph.add_or_update(MBNode(
            id="node_delete_db",
            domain="security",
            trigger_pattern={"exact_keys": ["DB全削除"]},
            action_template={
                "type": "tool_call",
                "payload": "DROP DATABASE prod;",
                "capability": "irreversible",  # ノード側で不可逆能力を明示
            },
            confidence=0.9,
        ))
        runtime = EnterpriseRuntime(mb_graph=graph, theta_0=2.0)
        efp = BusinessInput("T_SEC_IRREV", "U1", "security", "DB全削除を実行して")
        runtime.dispatch_ticket(efp)

        # ActionLedger に記録された action の capability が IRREVERSIBLE になっていること
        rec = runtime.canary_manager.action_ledger.records[-1]
        self.assertEqual(rec.ticket_id, "T_SEC_IRREV")
        self.assertEqual(rec.capability, ActionCapability.IRREVERSIBLE)
        self.assertFalse(rec.is_reversible)

    def test_h_canary_unified_with_h_state_and_resolution_result_current_h_updated(self):
        """H_canary 一本化 & 更新順修正: TicketResolutionResult.current_h が最新の熱を即座に返し、HState と一致すること"""
        runtime = EnterpriseRuntime(mb_graph=self.prod_graph, theta_0=2.0)
        prop = ReorganizationProposal(
            proposal_id="prop_h_unify",
            hot_node_id="node_wf",
            candidate_mb=self.candidate_graph,
            durability_test_result={"all_passed": True, "candidate_content_hash": self.candidate_graph.content_hash()},
            policy=PromotionPolicy(require_durability=True, require_shadow=False, require_human_approval=True),
            status=ProposalState.APPROVAL_READY,
        )
        runtime.pending_reorganizations["prop_h_unify"] = prop
        mgr = AuthorityContext(actor_id="mgr_01", role="manager", scope="workflow", actor_type="human", authenticated_by="idp_sso")
        runtime.promote_candidate_mb("prop_h_unify", authority=mgr, use_canary=True, canary_ratio=1.0, theta_canary=5.0)

        efp = BusinessInput("T_HEAT_SEQ", "U1", "workflow", "稟議申請の承認")
        runtime.dispatch_ticket(efp)

        # 失敗フィードバック (E_pred=1.0)
        res = runtime.resolve_ticket_feedback("T_HEAT_SEQ", FeedbackResult(user_resolved=False))

        # 返却された current_h が 0.0 (古い値) ではなく、今回の E を取り込んだ最新値であること
        self.assertGreater(res.current_h, 0.0)
        # HState の versioned_heats と CanaryManager.canary_heat が完全一致していること
        cand_ver = self.candidate_graph.version
        unified_h = runtime.h_state.version_total_heat(cand_ver)
        self.assertEqual(res.current_h, unified_h)
        self.assertEqual(runtime.canary_manager.active_deployment.canary_heat, unified_h)


if __name__ == "__main__":
    unittest.main()
