import unittest

from rdl_enterprise import AuthorityContext, StructuralConflict, StructuralConflictInbox


class StructuralConflictInboxTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
