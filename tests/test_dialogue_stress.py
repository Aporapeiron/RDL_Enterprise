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
    ProbeIntent,
    generate_question,
    record_human_response,
    reconstruct_dialogue_structure,
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

        observations = (initial, t2, t4, t6, t7, t9, t12)
        self.assertEqual(initial.utterance.startswith("会社"), True)
        self.assertEqual(t4.utterance != t6.utterance, True)
        self.assertEqual(t4.provenance, t6.provenance)
        self.assertEqual(t9.profiles[0].strength.support, EvidencePolarity.UNRESOLVED)
        self.assertEqual(reflection.context, b3)

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


if __name__ == "__main__":
    unittest.main()
