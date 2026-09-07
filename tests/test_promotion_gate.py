import unittest
from rdl_enterprise.mb_graph import MBGraph, MBNode
from rdl_enterprise.authority import AuthorityContext
from rdl_enterprise.snapshot import BusinessInput, FeedbackResult
from rdl_enterprise.shadow import ShadowReport
from rdl_enterprise.promotion_gate import ProposalState, PromotionPolicy, PromotionGate
from rdl_enterprise.runtime import EnterpriseRuntime, ReorganizationProposal

class TestPromotionGate(unittest.TestCase):

    def setUp(self):
        self.graph = MBGraph()
        self.node = MBNode(
            id="node_wf",
            domain="workflow",
            trigger_pattern={"exact_keys": ["稟議申請"]},
            action_template={"type": "direct_reply", "payload": "http://old.corp"},
            confidence=0.8,
        )
        self.graph.add_or_update(self.node)

    def test_promotion_rejected_when_durability_failed_even_with_admin_approval(self):
        """ガバナンス分離: 管理者(Admin)の承認があっても、Durability不合格なら昇格拒絶される"""
        runtime = EnterpriseRuntime(mb_graph=self.graph, theta_0=1.0)

        # 破断した耐久検査結果を持つプロポーザル
        bad_prop = ReorganizationProposal(
            proposal_id="prop_broken_01",
            hot_node_id="node_wf",
            candidate_mb=self.graph,
            durability_test_result={"all_passed": False, "reports": []},
            policy=PromotionPolicy(require_durability=True, require_shadow=False),
            status=ProposalState.DRAFT,
        )
        runtime.pending_reorganizations["prop_broken_01"] = bad_prop

        # 管理者が承認印を押そうとする
        admin = AuthorityContext(actor_id="admin_user", role="admin", scope="all")
        success = runtime.promote_candidate_mb("prop_broken_01", authority=admin)

        # 昇格は拒絶され、ステータスは rejected に遷移！
        self.assertFalse(success)
        self.assertEqual(bad_prop.status, ProposalState.REJECTED)
        self.assertIn("耐久検査", bad_prop.reasons[0])

    def test_promotion_blocked_when_shadow_evidence_insufficient(self):
        """証拠不足のブロック: Shadow必須ポリシーにおいて証拠不十分なら昇格拒絶"""
        runtime = EnterpriseRuntime(mb_graph=self.graph, theta_0=1.0)

        # Durabilityは合格したが、Shadowが0件のプロポーザル
        empty_shadow = ShadowReport(
            proposal_id="prop_shadow_01",
            total_shadow_cases=0,
            resolved_triplets_count=0,
            improved_count=0,
            regressed_count=0,
            unchanged_count=0,
            tier_improved_count=0,
            avg_confidence_delta=0.0,
            regression_rate=0.0,
            evaluation_status="insufficient_evidence",
            passed=False,
        )

        prop = ReorganizationProposal(
            proposal_id="prop_shadow_01",
            hot_node_id="node_wf",
            candidate_mb=self.graph,
            durability_test_result={"all_passed": True},
            policy=PromotionPolicy(require_durability=True, require_shadow=True, minimum_shadow_cases=1),
            shadow_report=empty_shadow,
            status=ProposalState.DURABILITY_PASSED,
        )
        runtime.pending_reorganizations["prop_shadow_01"] = prop

        mgr = AuthorityContext(actor_id="mgr_01", role="manager", scope="workflow")
        success = runtime.promote_candidate_mb("prop_shadow_01", authority=mgr)

        # 昇格拒絶され、ステータスが insufficient_evidence へ遷移
        self.assertFalse(success)
        self.assertEqual(prop.status, ProposalState.INSUFFICIENT_EVIDENCE)

    def test_state_machine_full_promotion_lifecycle(self):
        """状態機械の全サイクル: DRAFT -> DURABILITY_PASSED -> SHADOW_PASSED -> APPROVAL_READY -> PROMOTED"""
        runtime = EnterpriseRuntime(mb_graph=self.graph, theta_0=1.0)

        # 1. 候補 M_B' 起草
        candidate = MBGraph()
        candidate.add_or_update(MBNode(
            id="node_wf",
            domain="workflow",
            trigger_pattern={"exact_keys": ["稟議申請"]},
            action_template={"type": "direct_reply", "payload": "http://new-saas.corp"},
            confidence=0.9,
        ))

        passed_shadow = ShadowReport(
            proposal_id="prop_lifecycle_01",
            total_shadow_cases=1,
            resolved_triplets_count=1,
            improved_count=1,
            regressed_count=0,
            unchanged_count=0,
            tier_improved_count=0,
            avg_confidence_delta=0.1,
            regression_rate=0.0,
            evaluation_status="passed",
            passed=True,
        )

        prop = ReorganizationProposal(
            proposal_id="prop_lifecycle_01",
            hot_node_id="node_wf",
            candidate_mb=candidate,
            durability_test_result={"all_passed": True},
            policy=PromotionPolicy(require_durability=True, require_shadow=True, minimum_shadow_cases=1),
            shadow_report=passed_shadow,
            status=ProposalState.DURABILITY_PASSED,
        )
        runtime.pending_reorganizations["prop_lifecycle_01"] = prop

        mgr = AuthorityContext(actor_id="mgr_01", role="manager", scope="workflow")
        success = runtime.promote_candidate_mb("prop_lifecycle_01", authority=mgr)

        self.assertTrue(success)
        self.assertEqual(prop.status, ProposalState.PROMOTED)
        self.assertEqual(runtime.mb_graph.get("node_wf").action_template["payload"], "http://new-saas.corp")

    def test_high_risk_policy_blocks_auto_promotion_even_with_delegated_authority(self):
        """高リスクドメイン(security等)では、委任権限があっても自動昇格が拒絶される"""
        sec_graph = MBGraph()
        sec_graph.add_or_update(MBNode(
            id="node_root",
            domain="security",
            trigger_pattern={"exact_keys": ["特権申請"]},
            action_template={"type": "human_escalation", "payload": "要承認"},
            authority_level="human_only",
        ))

        # security ドメインは default_for_domain で risk_level="high", require_human_approval=True
        sec_auth = AuthorityContext(actor_id="sec_bot", role="admin", scope="security")
        runtime = EnterpriseRuntime(
            mb_graph=sec_graph,
            theta_0=1.0,
            auto_promote_reorganizations=True,
            auto_promote_authority=sec_auth,
        )

        efp = BusinessInput("T_SEC", "U01", "security", "特権申請")
        runtime.handle_ticket(
            efp,
            feedback=FeedbackResult(user_resolved=False, human_rejected=True, new_knowledge_provided="要田中承認"),
        )

        # 自動昇格はブロックされ、プロポーザルは保留中のまま残る（人間承認待ち）
        self.assertEqual(len(runtime.pending_reorganizations), 1)
        prop_id = list(runtime.pending_reorganizations.keys())[0]
        self.assertNotEqual(runtime.pending_reorganizations[prop_id].status, ProposalState.PROMOTED)

if __name__ == "__main__":
    unittest.main()