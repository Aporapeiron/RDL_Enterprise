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
            unique_queries_count=1,
            covered_categories=["workflow"],
            diversity_score=1.0,
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

        mgr = AuthorityContext(
            actor_id="mgr_01",
            role="manager",
            scope="workflow",
            actor_type="human",
            authenticated_by="idp_sso",
        )
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
        sec_auth = AuthorityContext(
            actor_id="sec_bot",
            role="admin",
            scope="security",
            actor_type="agent",
            authenticated_by="delegated_agent",
        )
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

    def test_human_identity_proof_rejects_agent_or_api_key_even_if_is_automated_false(self):
        """自己詐称防止: is_automated=False と偽装しても、actor_type!=human や api_key は拒絶される"""
        from rdl_enterprise.promotion_gate import EvidenceRequirement
        runtime = EnterpriseRuntime(mb_graph=self.graph, theta_0=1.0)
        prop = ReorganizationProposal(
            proposal_id="prop_spoof_01",
            hot_node_id="node_wf",
            candidate_mb=self.graph,
            durability_test_result={"all_passed": True},
            policy=PromotionPolicy(
                require_durability=True,
                require_shadow=False,
                require_human_approval=True,
            ),
            status=ProposalState.APPROVAL_READY,
        )
        runtime.pending_reorganizations["prop_spoof_01"] = prop

        # ケースA: admin ロールだが actor_type="agent" (プログラムボットによる偽装呼び出し)
        agent_admin = AuthorityContext(
            actor_id="bot_script",
            role="admin",
            scope="all",
            actor_type="agent",
            authenticated_by="api_key",
        )
        # is_automated=False (手動呼び出しを装う)
        success_agent = runtime.promote_candidate_mb("prop_spoof_01", authority=agent_admin, is_automated=False)
        self.assertFalse(success_agent)
        self.assertEqual(prop.status, ProposalState.REJECTED)
        self.assertIn("人間承認", prop.reasons[-1])

        # 再度プロポーザルをセット
        prop.status = ProposalState.APPROVAL_READY
        runtime.pending_reorganizations["prop_spoof_01"] = prop

        # ケースB: actor_type="human" だが authenticated_by="api_key" (対人認証基盤を経ていない)
        unverified_human = AuthorityContext(
            actor_id="hacker_impersonator",
            role="admin",
            scope="all",
            actor_type="human",
            authenticated_by="api_key",
        )
        success_unverified = runtime.promote_candidate_mb("prop_spoof_01", authority=unverified_human, is_automated=False)
        self.assertFalse(success_unverified)
        self.assertEqual(prop.status, ProposalState.REJECTED)

        # ケースC: 正真正銘の認証済み人間 (IdP SSO / MFA) -> 昇格成功
        prop.status = ProposalState.APPROVAL_READY
        runtime.pending_reorganizations["prop_spoof_01"] = prop
        real_human = AuthorityContext(
            actor_id="tanaka_admin",
            role="admin",
            scope="all",
            actor_type="human",
            authenticated_by="idp_sso",
        )
        success_real = runtime.promote_candidate_mb("prop_spoof_01", authority=real_human, is_automated=False)
        self.assertTrue(success_real)
        self.assertEqual(prop.status, ProposalState.PROMOTED)

    def test_evidence_coverage_rejects_monotonous_identical_queries(self):
        """多様性要件: 解決件数は充足していても、すべて同一クエリ(偏り)の場合は INSUFFICIENT_EVIDENCE"""
        from rdl_enterprise.promotion_gate import EvidenceRequirement
        runtime = EnterpriseRuntime(mb_graph=self.graph, theta_0=1.0)

        # 5件解決しているが、すべて「同一クエリ」の偏ったレポート (unique_queries_count = 1)
        monotonous_shadow = ShadowReport(
            proposal_id="prop_mono_01",
            total_shadow_cases=5,
            resolved_triplets_count=5,
            improved_count=5,
            regressed_count=0,
            unchanged_count=0,
            tier_improved_count=0,
            avg_confidence_delta=0.1,
            regression_rate=0.0,
            evaluation_status="passed",
            passed=True,
            unique_queries_count=1,  # 1種類しかない！
            covered_categories=["workflow"],
            diversity_score=0.2,
        )

        # ポリシーでユニークパターン数 >= 2 を要求
        policy = PromotionPolicy(
            require_durability=True,
            require_shadow=True,
            evidence_requirement=EvidenceRequirement(
                minimum_cases=2,
                minimum_unique_patterns=2,
            ),
        )

        res = PromotionGate.evaluate_readiness(
            current_state=ProposalState.SHADOW_RUNNING,
            durability_result={"all_passed": True},
            shadow_report=monotonous_shadow,
            policy=policy,
        )

        self.assertFalse(res.can_promote)
        self.assertEqual(res.next_state, ProposalState.INSUFFICIENT_EVIDENCE)
        self.assertIn("多様性が不足", res.reasons[0])

    def test_evidence_coverage_rejects_missing_required_categories(self):
        """境界網羅要件: 必須カテゴリがシャドウでカバーされていない場合は INSUFFICIENT_EVIDENCE"""
        from rdl_enterprise.promotion_gate import EvidenceRequirement

        # workflow だけカバーされたレポート
        partial_shadow = ShadowReport(
            proposal_id="prop_partial_01",
            total_shadow_cases=3,
            resolved_triplets_count=3,
            improved_count=2,
            regressed_count=0,
            unchanged_count=1,
            tier_improved_count=0,
            avg_confidence_delta=0.1,
            regression_rate=0.0,
            evaluation_status="passed",
            passed=True,
            unique_queries_count=3,
            covered_categories=["workflow"],
            diversity_score=1.0,
        )

        # workflow と security の両ドメインの網羅を要求
        policy = PromotionPolicy(
            require_durability=True,
            require_shadow=True,
            evidence_requirement=EvidenceRequirement(
                minimum_cases=2,
                minimum_unique_patterns=2,
                required_categories=["workflow", "security"],
            ),
        )

        res = PromotionGate.evaluate_readiness(
            current_state=ProposalState.SHADOW_RUNNING,
            durability_result={"all_passed": True},
            shadow_report=partial_shadow,
            policy=policy,
        )

        self.assertFalse(res.can_promote)
        self.assertEqual(res.next_state, ProposalState.INSUFFICIENT_EVIDENCE)
        self.assertIn("必須カテゴリが未カバー", res.reasons[0])

    def test_evidence_coverage_rejects_when_no_improved_cases_under_strict_policy(self):
        """改善実証要件: require_improved_case=True で改善事例が0件の場合は INSUFFICIENT_EVIDENCE"""
        from rdl_enterprise.promotion_gate import EvidenceRequirement

        # 退行はない(regressed=0)が、改善も0件(unchangedばかり)
        unchanged_shadow = ShadowReport(
            proposal_id="prop_unchanged_01",
            total_shadow_cases=2,
            resolved_triplets_count=2,
            improved_count=0,       # 改善なし
            regressed_count=0,
            unchanged_count=2,
            tier_improved_count=0,
            avg_confidence_delta=0.0,
            regression_rate=0.0,
            evaluation_status="passed",
            passed=True,
            unique_queries_count=2,
            covered_categories=["security"],
            diversity_score=1.0,
        )

        policy = PromotionPolicy(
            require_durability=True,
            require_shadow=True,
            evidence_requirement=EvidenceRequirement(
                minimum_cases=2,
                minimum_unique_patterns=2,
                require_improved_case=True,
            ),
        )

        res = PromotionGate.evaluate_readiness(
            current_state=ProposalState.SHADOW_RUNNING,
            durability_result={"all_passed": True},
            shadow_report=unchanged_shadow,
            policy=policy,
        )

        self.assertFalse(res.can_promote)
        self.assertEqual(res.next_state, ProposalState.INSUFFICIENT_EVIDENCE)
        self.assertIn("改善実績", res.reasons[0])


if __name__ == "__main__":
    unittest.main()