import unittest
from rdl_core import ConstraintIdentity, ConstraintStrength, EvidencePolarity, Provenance, RelationConstraintProfile

from rdl_enterprise import AuthorityContext, BusinessInput, EnterpriseRuntime, MBGraph, StructuralConflict, StructuralConflictInbox


class StructuralConflictInboxTests(unittest.TestCase):
    REALISTIC_CONFLICT_CASES = (
        ("IT-101", ("user", "may_reset_mfa", "without_manager_approval"),
         "incident-recovery", "security-policy", 3),
        ("IT-102", ("contractor", "may_access", "production"),
         "incident-runbook", "access-policy", 2),
        ("IT-103", ("device", "must_preserve", "user_data"),
         "legal-hold", "offboarding-policy", 1),
        ("IT-104", ("server", "must_restart", "now"),
         "security-advisory", "availability-rule", 1),
        ("IT-105", ("support", "may_disclose", "account_information"),
         "recovery-procedure", "privacy-policy", 4),
        ("IT-106", ("service", "may_disable", "user_account"),
         "security-response", "business-continuity", 2),
    )

    def test_lists_cases_by_bounded_sum_and_preserves_evidence(self):
        inbox = StructuralConflictInbox()
        inbox.add_conflict(StructuralConflict(
            "c1", "IT-3", "mb-a", "mb-b", 0.7,
            heat_components=(("authority", 0.7),),
            authority_requirements=("manager",),
            support_by_structure=(("mb-a", 0.8), ("mb-b", 0.2)),
            provenance="inspection-1",
        ))
        inbox.add_conflict(StructuralConflict("c2", "IT-4", "mb-x", "mb-y", 0.9))
        inbox.add_conflict(StructuralConflict("c3", "IT-3", "mb-a", "mb-c", 0.4))

        cases = inbox.list_cases()
        self.assertEqual([item.case_id for item in cases], ["IT-3", "IT-4"])
        self.assertAlmostEqual(cases[0].total_predicted_heat, 1.1)
        detail = inbox.detail("IT-3")
        self.assertEqual(detail["status"], "STRUCTURAL_CONFLICT")
        self.assertEqual(detail["conflicts"][0]["provenance"], "inspection-1")

    def test_support_is_evidence_not_resolution_and_human_route_is_explicit(self):
        inbox = StructuralConflictInbox()
        inbox.add_conflict(StructuralConflict("c1", "IT-3", "a", "b", 0.5,
                                               support_by_structure=(("a", 1.0), ("b", 1.0))))
        self.assertEqual(inbox.get_case("IT-3").review_status, "STRUCTURAL_CONFLICT")
        actor = AuthorityContext("mgr", "manager", "workflow", "human", "idp_sso")
        routed = inbox.route_to_human("IT-3", actor)
        self.assertTrue(routed.human_routed)
        self.assertEqual(routed.routed_actor_id, "mgr")
        self.assertEqual(inbox.get_case("IT-3").review_status, "STRUCTURAL_CONFLICT")

    def test_unknown_and_invalid_heat_are_not_collapsed(self):
        inbox = StructuralConflictInbox()
        with self.assertRaises(ValueError):
            StructuralConflict("c1", "IT-3", "a", "b", 1.1)
        self.assertEqual(inbox.history(), ())

    def test_explicit_observation_calculates_heat_and_preserves_decision_history(self):
        inbox = StructuralConflictInbox()
        item = inbox.observe_conflict(
            conflict_id="c1", case_id="IT-31", left_structure_id="security",
            right_structure_id="recovery", incompatible=True,
            heat_components=(("authority", 0.4), ("support_gap", 0.22)),
            authority_requirements=("security-manager",), provenance="inspection-31",
        )
        self.assertAlmostEqual(item.total_predicted_heat, 0.62)
        actor = AuthorityContext("mgr", "manager", "workflow", "human", "idp_sso")
        inbox.record_decision("IT-31", "defer", actor)
        events = inbox.history("IT-31")
        self.assertEqual(events[-1].decision, "defer")
        self.assertEqual(inbox.get_case("IT-31").review_status, "STRUCTURAL_CONFLICT")
        with self.assertRaises(ValueError):
            inbox.observe_conflict(conflict_id="c2", case_id="IT-31",
                                   left_structure_id="a", right_structure_id="b",
                                   incompatible=False, heat_components=(("x", 0.1),))

    def test_counterfactual_changes_conflict_count_without_mutating_inbox(self):
        inbox = StructuralConflictInbox()
        inbox.observe_conflict(conflict_id="c1", case_id="IT-31", left_structure_id="a",
                               right_structure_id="b", incompatible=True,
                               heat_components=(("x", 0.6),))
        inbox.observe_conflict(conflict_id="c2", case_id="IT-31", left_structure_id="a",
                               right_structure_id="c", incompatible=True,
                               heat_components=(("x", 0.2),))
        counterfactual = inbox.counterfactual_case("IT-31", ("a", "b"))
        self.assertEqual(len(counterfactual.conflicts), 1)
        self.assertEqual(len(inbox.get_case("IT-31").conflicts), 2)

    def test_active_structure_detector_routes_only_explicit_incompatibility(self):
        inbox = StructuralConflictInbox()
        items = inbox.detect_active_conflicts(
            case_id="IT-31", active_structure_ids=("security", "recovery", "cost"),
            compatibility_check=lambda left, right: False if {left, right} == {"security", "recovery"} else True,
            heat_components=lambda left, right: (("pair", 0.62),),
            provenance="active-inspection-31",
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].conflicts[0].observation_status, "STRUCTURAL_CONFLICT")
        self.assertEqual(inbox.get_case("IT-31").conflicts[0].left_structure_id, "security")

    def test_existing_rdl_profiles_feed_real_structural_conflict_detection(self):
        key = ("customer", "requires", "refund")
        profiles = {
            "customer-rule": RelationConstraintProfile(
                ConstraintIdentity("r1", *key, Provenance("customer")),
                ConstraintStrength(0.9, EvidencePolarity.SUPPORT)),
            "policy-rule": RelationConstraintProfile(
                ConstraintIdentity("r2", *key, Provenance("policy")),
                ConstraintStrength(0.8, EvidencePolarity.OPPOSE)),
            "other-rule": RelationConstraintProfile(
                ConstraintIdentity("r3", "customer", "owns", "account", Provenance("other")),
                ConstraintStrength(0.7, EvidencePolarity.SUPPORT)),
        }
        inbox = StructuralConflictInbox()
        items = inbox.detect_relation_profile_conflicts(case_id="IT-31", active_profiles=profiles)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].conflicts[0].left_structure_id, "customer-rule")

    def test_realistic_enterprise_conflicts_enter_inbox_without_priority_override(self):
        for case_id, key, support_id, oppose_id, _human_priority in self.REALISTIC_CONFLICT_CASES:
            with self.subTest(case_id=case_id):
                profiles = {
                    support_id: RelationConstraintProfile(
                        ConstraintIdentity(f"{case_id}-support", *key,
                                           Provenance(support_id)),
                        ConstraintStrength(0.95, EvidencePolarity.SUPPORT)),
                    oppose_id: RelationConstraintProfile(
                        ConstraintIdentity(f"{case_id}-oppose", *key,
                                           Provenance(oppose_id)),
                        ConstraintStrength(0.90, EvidencePolarity.OPPOSE)),
                }
                inbox = StructuralConflictInbox()

                items = inbox.detect_relation_profile_conflicts(
                    case_id=case_id, active_profiles=profiles,
                    provenance=f"realistic-fixture:{case_id}")

                self.assertEqual(len(items), 1)
                conflict = items[0].conflicts[0]
                self.assertEqual(conflict.observation_status, "STRUCTURAL_CONFLICT")
                self.assertEqual(conflict.left_structure_id, support_id)
                self.assertEqual(conflict.right_structure_id, oppose_id)
                self.assertEqual(conflict.predicted_heat, 1.0)
                self.assertEqual(conflict.provenance, f"realistic-fixture:{case_id}")

    def test_realistic_fixture_priority_is_observation_not_current_heat_policy(self):
        inbox = StructuralConflictInbox()
        observed = {}
        for case_id, key, support_id, oppose_id, human_priority in self.REALISTIC_CONFLICT_CASES:
            profiles = {
                support_id: RelationConstraintProfile(
                    ConstraintIdentity(f"{case_id}-support", *key,
                                       Provenance(support_id)),
                    ConstraintStrength(0.95, EvidencePolarity.SUPPORT)),
                oppose_id: RelationConstraintProfile(
                    ConstraintIdentity(f"{case_id}-oppose", *key,
                                       Provenance(oppose_id)),
                    ConstraintStrength(0.90, EvidencePolarity.OPPOSE)),
            }
            observed[case_id] = (
                inbox.detect_relation_profile_conflicts(
                    case_id=case_id, active_profiles=profiles)[0].total_predicted_heat,
                human_priority,
            )

        self.assertEqual({heat for heat, _priority in observed.values()}, {1.0})
        self.assertGreater(len({priority for _heat, priority in observed.values()}), 1)

    def test_unresolved_relation_polarity_is_not_promoted_to_conflict(self):
        key = ("customer", "requires", "refund")
        profiles = {
            "known": RelationConstraintProfile(
                ConstraintIdentity("r1", *key, Provenance("known")),
                ConstraintStrength(0.9, EvidencePolarity.SUPPORT)),
            "unknown": RelationConstraintProfile(
                ConstraintIdentity("r2", *key, Provenance("unknown")),
                ConstraintStrength(1.0, EvidencePolarity.UNRESOLVED)),
        }
        inbox = StructuralConflictInbox()

        items = inbox.detect_relation_profile_conflicts(
            case_id="IT-32", active_profiles=profiles)

        self.assertEqual(items, ())
        self.assertEqual(inbox.list_cases(), ())

    def test_profile_support_strength_does_not_resolve_opposite_polarity(self):
        key = ("customer", "requires", "refund")
        profiles = {
            "strong-support": RelationConstraintProfile(
                ConstraintIdentity("r1", *key, Provenance("strong")),
                ConstraintStrength(1.0, EvidencePolarity.SUPPORT)),
            "weak-opposition": RelationConstraintProfile(
                ConstraintIdentity("r2", *key, Provenance("weak")),
                ConstraintStrength(0.1, EvidencePolarity.OPPOSE)),
        }
        inbox = StructuralConflictInbox()

        items = inbox.detect_relation_profile_conflicts(
            case_id="IT-33", active_profiles=profiles)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].review_status, "STRUCTURAL_CONFLICT")
        self.assertEqual(items[0].conflicts[0].observation_status,
                         "STRUCTURAL_CONFLICT")

    def test_runtime_dispatch_exposes_conflict_scalar_without_mutating_mb(self):
        key = ("customer", "requires", "refund")
        profiles = {
            "a": RelationConstraintProfile(ConstraintIdentity("a", *key, Provenance("a")),
                                            ConstraintStrength(0.9, EvidencePolarity.SUPPORT)),
            "b": RelationConstraintProfile(ConstraintIdentity("b", *key, Provenance("b")),
                                            ConstraintStrength(0.8, EvidencePolarity.OPPOSE)),
        }
        graph = MBGraph()
        before = graph.content_hash()
        runtime = EnterpriseRuntime(
            mb_graph=graph,
            relation_profile_provider=lambda efp, prediction, active_graph: profiles,
        )
        result = runtime.handle_ticket(BusinessInput("IT-31", "customer", "workflow", "refund"))
        self.assertEqual(result.structural_conflict_status, "STRUCTURAL_CONFLICT")
        self.assertEqual(result.structural_conflict_count, 1)
        self.assertGreater(result.predicted_conflict_heat, 0.0)
        self.assertEqual(graph.content_hash(), before)
        self.assertEqual(runtime.conflict_inbox.get_case("IT-31").conflicts[0].observation_status,
                         "STRUCTURAL_CONFLICT")

    def test_runtime_uses_cumulative_inbox_summary_once_for_multiple_conflicts(self):
        key = ("customer", "requires", "refund")
        profiles = {
            "support-a": RelationConstraintProfile(
                ConstraintIdentity("r1", *key, Provenance("a")),
                ConstraintStrength(1.0, EvidencePolarity.SUPPORT)),
            "oppose": RelationConstraintProfile(
                ConstraintIdentity("r2", *key, Provenance("b")),
                ConstraintStrength(0.8, EvidencePolarity.OPPOSE)),
            "support-c": RelationConstraintProfile(
                ConstraintIdentity("r3", *key, Provenance("c")),
                ConstraintStrength(0.2, EvidencePolarity.SUPPORT)),
        }
        runtime = EnterpriseRuntime(
            mb_graph=MBGraph(),
            relation_profile_provider=lambda efp, prediction, active_graph: profiles,
        )

        result = runtime.handle_ticket(BusinessInput("IT-34", "customer", "workflow", "refund"))

        self.assertEqual(result.structural_conflict_status, "STRUCTURAL_CONFLICT")
        self.assertEqual(result.structural_conflict_count, 2)
        self.assertAlmostEqual(result.predicted_conflict_heat, 2.0)
        self.assertEqual(len(runtime.conflict_inbox.get_case("IT-34").conflicts), 2)


if __name__ == "__main__":
    unittest.main()
