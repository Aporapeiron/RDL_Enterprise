import unittest

from rdl_core import (
    AdaptiveMBProfile,
    BoundaryContext,
    ConstraintIdentity,
    ConstraintStrength,
    EvidencePolarity,
    Provenance,
    RelationConstraintProfile,
    extract_structure_candidate,
)
from rdl_enterprise.dialogue_probe import (
    DialogueObservation,
    DialogueStructureRevision,
    ProbeIntent,
    generate_question,
    record_human_response,
    reconstruct_dialogue_structure,
    reconstruct_selected_dialogue_structure,
    select_probe_intent,
)


class TestDialogueStress(unittest.TestCase):
    def test_dialogue_delta_forms_finite_structure_without_truth_or_diagnosis(self):
        source = Provenance("dialogue-participant", lineage="conversation-01")
        b1 = BoundaryContext("dialogue-t1", question="what is difficult", purpose="exploration")
        b2 = BoundaryContext("dialogue-t2", question="what differs", purpose="exploration")
        b3 = BoundaryContext("dialogue-t3", question="what to do next", purpose="planning")

        def profile(cid, relation, object_name, strength, polarity):
            return RelationConstraintProfile(
                ConstraintIdentity(cid, "person", relation, object_name),
                ConstraintStrength(strength, polarity),
            )

        p0 = profile("t1-unknown", "cause", "work-distress", 0.5, EvidencePolarity.UNRESOLVED)
        initial = record_human_response(
            "t1", "会社に行きたくない。でも辞めたいわけでもない。何が嫌か分からない。",
            (p0,), b1, source,
        )
        intent = select_probe_intent((p0,), b1, provenance=source)
        question = generate_question(intent)
        self.assertIsInstance(intent, ProbeIntent)
        self.assertNotEqual(intent, question)
        self.assertEqual(intent.context, question.context)
        self.assertIn("diagnosis", intent.avoid)

        t2 = record_human_response(
            "t2", "一人で作業する日は比較的つらくありません。",
            (profile("t2-solitary", "solitary-work", "less-distress", 0.8, EvidencePolarity.SUPPORT),),
            b2, source, question=question,
        )
        t4 = record_human_response(
            "t4", "評価されるのが嫌なのかもしれません。",
            (profile("t4-evaluation", "evaluation", "causes-distress", 0.65, EvidencePolarity.SUPPORT),),
            b2, source,
        )
        t6 = record_human_response(
            "t6", "いや、評価そのものではない気がします。",
            (profile("t6-evaluation-revised", "evaluation", "causes-distress", 0.4, EvidencePolarity.OPPOSE),),
            b2, source,
        )
        t7 = record_human_response(
            "t7", "期待が分からないまま判断して、後から違うと言われるのが嫌です。",
            (
                profile("t7-expectation", "unclear-expectations", "causes-distress", 0.9, EvidencePolarity.SUPPORT),
                profile("t7-retrospective", "after-the-fact-correction", "causes-distress", 0.85, EvidencePolarity.SUPPORT),
            ), b2, source,
        )
        t9 = record_human_response(
            "t9", "上司との関係が原因かは分かりません。",
            (profile("t9-manager", "manager-relationship", "causes-distress", 0.5, EvidencePolarity.UNRESOLVED),),
            b2, source,
        )
        reflection_intent = ProbeIntent(
            "structure_reflection", ("unclear-expectations", "after-the-fact-correction"),
            "reconstruction", b3, avoid=("diagnosis",), provenance=source,
        )
        reflection = generate_question(reflection_intent)
        t12 = record_human_response(
            "t12", "役割が曖昧なまま判断させられる点は合っていますが、仕事量も少し関係します。",
            (profile("t12-workload", "workload", "causes-distress", 0.35, EvidencePolarity.SUPPORT),),
            b3, source, question=reflection,
        )
        t13 = record_human_response(
            "t13", "来週は、期待される役割を先に確認してから判断したいです。",
            (profile("t13-role-clarification", "role-clarification", "reduces-risk", 0.85, EvidencePolarity.SUPPORT),),
            b3, source,
        )
        t10 = record_human_response(
            "t10", "長期的に辞めたいかどうかは、まだ分かりません。",
            (profile("t10-long-term", "long-term-exit", "preferred", 0.5, EvidencePolarity.UNRESOLVED),),
            b3, source,
        )
        t11_intent = ProbeIntent(
            "structure_reflection",
            ("unclear-expectations", "after-the-fact-correction", "workload"),
            "reflection",
            b3,
            avoid=("diagnosis", "forced_choice"),
            provenance=source,
        )
        t11_question = generate_question(t11_intent)
        t11 = record_human_response(
            "t11", "役割の曖昧さと後から基準が変わることは合っています。仕事量だけではありません。",
            (profile("t11-workload-correction", "workload", "primary-cause", 0.2, EvidencePolarity.OPPOSE),),
            b3, source, question=t11_question,
        )
        t14 = record_human_response(
            "t14", "まず来週は役割と判断基準を確認し、二週間だけ様子を見ます。",
            (profile("t14-limited-plan", "clarify-and-observe", "case-action", 0.9, EvidencePolarity.SUPPORT),),
            b3, source,
        )

        observations = (initial, t2, t4, t6, t7, t9, t10, t11, t12, t13, t14)
        self.assertEqual(initial.utterance.startswith("会社"), True)
        self.assertEqual(t4.utterance != t6.utterance, True)
        self.assertEqual(t4.provenance, t6.provenance)
        self.assertEqual(t9.profiles[0].strength.support, EvidencePolarity.UNRESOLVED)
        self.assertEqual(reflection.context, b3)
        self.assertEqual(t13.context.purpose, "planning")
        self.assertEqual(t13.profiles[0].identity.semantic_key.relation, "role-clarification")
        self.assertEqual(t10.profiles[0].strength.support, EvidencePolarity.UNRESOLVED)
        self.assertEqual(t11_question.context, b3)
        self.assertEqual(t14.profiles[0].identity.semantic_key.relation, "clarify-and-observe")

        candidate = reconstruct_dialogue_structure(observations, b3, provenance=source)
        self.assertEqual(candidate.context, b3)
        self.assertEqual(candidate.provenance, source)
        self.assertTrue(any(p.identity.semantic_key.relation == "unclear-expectations" for p in candidate.supporting_profiles))
        self.assertGreaterEqual(candidate.unresolved_count, 1)
        self.assertFalse(hasattr(candidate, "truth"))
        self.assertFalse(hasattr(candidate, "diagnosis"))

        # Earlier observations remain independently recoverable after M_B'.
        self.assertEqual(observations[2].turn_id, "t4")
        self.assertEqual(observations[3].turn_id, "t6")
        self.assertEqual(observations[5].context, b2)
        self.assertEqual(observations[-1].context, b3)
        self.assertEqual(observations[0].turn_id, "t1")
        self.assertEqual(len(observations), 11)
        self.assertEqual(observations[-1].context.purpose, "planning")

        # Explicit T1 selection creates M_B1 first, then reconstructs M_B2
        # from selected planning turns. Boundary drift is not silently merged.
        mb1 = reconstruct_selected_dialogue_structure(
            observations, b2,
            selected_turn_ids=("t2", "t4", "t6", "t7", "t9"),
            provenance=source,
        )
        mb2 = reconstruct_selected_dialogue_structure(
            observations, b3,
            selected_turn_ids=("t10", "t11", "t12", "t13", "t14"),
            previous=mb1.current,
            provenance=source,
        )
        self.assertIsInstance(mb1, DialogueStructureRevision)
        self.assertIs(mb2.previous, mb1.current)
        self.assertEqual(mb1.context, b2)
        self.assertEqual(mb2.context, b3)
        self.assertEqual(mb1.selected_turn_ids, ("t2", "t4", "t6", "t7", "t9"))
        self.assertEqual(mb2.selected_turn_ids, ("t10", "t11", "t12", "t13", "t14"))
        self.assertGreaterEqual(mb2.current.unresolved_count, 1)
        with self.assertRaises(ValueError):
            reconstruct_selected_dialogue_structure(
                observations, b3, selected_turn_ids=("t14", "missing"),
            )
        with self.assertRaises(ValueError):
            reconstruct_selected_dialogue_structure(
                observations, b3, selected_turn_ids=("t14", "t14"),
            )
        with self.assertRaises(ValueError):
            reconstruct_selected_dialogue_structure(
                observations, b3, selected_turn_ids=("t2",),
            )
        with self.assertRaises(ValueError):
            reconstruct_selected_dialogue_structure(
                observations + (t14,), b3,
                selected_turn_ids=("t14",),
            )


if __name__ == "__main__":
    unittest.main()
