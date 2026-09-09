import ast
import unittest

from rdl_core import (
    AuthorityConstraint,
    BoundaryContext,
    CommitmentOrigin,
    CommitmentRecord,
    EvidencePolarity,
    Provenance,
    ConstraintActivation,
    ConstraintEvaluationWeights,
    ConstraintIdentity,
    ConstraintStrength,
    evaluate_constraint_strength,
)


class TestCoreContracts(unittest.TestCase):
    def test_core_contracts_have_no_enterprise_dependency(self):
        self.assertEqual(EvidencePolarity.UNRESOLVED.value, "unresolved")
        self.assertEqual(CommitmentOrigin.AUTHORITY.value, "authority")
        self.assertEqual(BoundaryContext("b1").boundary_id, "b1")
        self.assertEqual(Provenance("fixture").source, "fixture")
        self.assertEqual(AuthorityConstraint("a1", "workflow", "ticket", "approve").scope, "workflow")

    def test_commitment_record_requires_valid_origin_and_time(self):
        record = CommitmentRecord.from_dict_strict(
            {
                "origin": "authority",
                "committed_at": "2026-09-09T00:00:00+00:00",
                "actor": "operator-1",
            }
        )
        self.assertEqual(record.origin, "authority")
        with self.assertRaises(ValueError):
            CommitmentRecord.from_dict_strict(
                {"origin": "truth", "committed_at": "bad", "actor": "operator-1"}
            )

    def test_domain_origin_is_namespaced_and_boundary_conditions_are_deep_frozen(self):
        record = CommitmentRecord.from_dict_strict(
            {
                "origin": "game:rumor",
                "committed_at": "2026-09-09T00:00:00+00:00",
                "actor": "npc-1",
            }
        )
        self.assertEqual(record.origin, "game:rumor")
        context = BoundaryContext("b1", conditions={"seed": 1, "nested": {"mode": "test"}})
        with self.assertRaises(TypeError):
            context.conditions["seed"] = 999
        with self.assertRaises(TypeError):
            context.conditions["nested"]["mode"] = "mutated"

        tuple_context = BoundaryContext("b2", conditions={"items": ({"seed": 1},)})
        with self.assertRaises(TypeError):
            tuple_context.conditions["items"][0]["seed"] = 2
        with self.assertRaises(TypeError):
            BoundaryContext("b3", conditions={"unsupported": object()})

    def test_core_source_has_no_runtime_package_imports(self):
        from pathlib import Path

        core_root = Path(__file__).parents[1] / "src" / "rdl_core"
        forbidden = {"rdl_enterprise", "rdl_simulation"}
        for path in core_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    imported = {node.module.split(".")[0]} if node.module else set()
                else:
                    continue
                self.assertTrue(forbidden.isdisjoint(imported), f"forbidden import in {path}: {imported & forbidden}")

    def test_legacy_contract_compatibility_fixtures(self):
        fixtures = [
            ({"origin": "authority", "committed_at": "2026-09-09T00:00:00+00:00", "actor": "a1"}, True),
            ({"origin": "truth", "committed_at": "2026-09-09T00:00:00+00:00", "actor": "a1"}, False),
            ({"origin": "authority", "committed_at": "bad", "actor": "a1"}, False),
            ({"origin": "authority", "committed_at": "2026-09-09T00:00:00+00:00", "actor": "a1", "evidence_at": 123}, False),
        ]
        for payload, accepted in fixtures:
            try:
                record = CommitmentRecord.from_dict_strict(payload)
                observed = True
                serialized = record.to_dict()
            except ValueError:
                observed = False
                serialized = None
            self.assertEqual(observed, accepted, payload)
            if accepted:
                self.assertEqual(serialized["origin"], payload["origin"])

    def test_intentional_namespaced_origin_expansion(self):
        record = CommitmentRecord.from_dict_strict(
            {"origin": "game:rumor", "committed_at": "2026-09-09T00:00:00+00:00", "actor": "npc-1"}
        )
        self.assertEqual(record.origin, "game:rumor")

    def test_constraint_identity_and_bounded_activation(self):
        identity = ConstraintIdentity("c1", "requester", "may_approve", "expense")
        activation = ConstraintActivation(
            identity=identity,
            context=BoundaryContext("b1", question="approval"),
            strength=ConstraintStrength(value=0.8, support=EvidencePolarity.SUPPORT, relevance=0.9),
            authority_constraint=AuthorityConstraint("a1", "finance", "expense", "approve"),
        )
        self.assertEqual(activation.identity.constraint_id, "c1")
        with self.assertRaises(ValueError):
            ConstraintStrength(value=1.1)
        with self.assertRaises(TypeError):
            ConstraintStrength(value=0.5, support="truth")
        with self.assertRaises(ValueError):
            ConstraintIdentity("c2", 123, "relates", "object")

    def test_constraint_strength_evaluation_is_bounded_and_polarity_explicit(self):
        strength = evaluate_constraint_strength(
            relevance=1.0,
            freshness=0.5,
            authority=0.0,
            source=0.5,
            convergence=1.0,
            support=EvidencePolarity.OPPOSE,
            weights=ConstraintEvaluationWeights(
                relevance=1.0, freshness=1.0, authority=0.0, source=0.0, convergence=0.0
            ),
        )
        self.assertEqual(strength.support, EvidencePolarity.OPPOSE)
        self.assertEqual(strength.value, 0.75)
        with self.assertRaises(ValueError):
            evaluate_constraint_strength(
                relevance=1.1, freshness=0.5, authority=0.5, source=0.5, convergence=0.5
            )

    def test_enterprise_bundle_adapter_preserves_bounded_observation(self):
        from types import SimpleNamespace
        from rdl_enterprise.constraint_adapter import activation_from_bundle

        bundle = SimpleNamespace(
            constraint_score=0.7,
            relevance=0.8,
            freshness=0.6,
            authority_weight=0.4,
        )
        activation = activation_from_bundle(
            bundle,
            ConstraintIdentity("c-adapter", "requester", "may_approve", "expense"),
            BoundaryContext("b-adapter", question="approval"),
            support=EvidencePolarity.UNRESOLVED,
        )
        self.assertEqual(activation.strength.value, 0.7)
        self.assertEqual(activation.strength.support, EvidencePolarity.UNRESOLVED)

    def test_enterprise_core_bounded_equivalence_selected_observation(self):
        from rdl_enterprise.constraint import ConstraintBundle
        from rdl_enterprise.constraint_adapter import activation_from_bundle

        bundle = ConstraintBundle(
            node_ids=["n-primary", "n-support"],
            constraint_score=0.72,
            relevance=0.81,
            freshness=0.63,
            authority_weight=0.41,
        )
        identity = ConstraintIdentity(
            "bundle:c1", "requester", "may_approve", "expense"
        )
        context = BoundaryContext(
            "run:bounded-1",
            question="approval",
            purpose="bounded-equivalence",
            conditions={"mb_version": "prod", "observation": "selected"},
        )
        activation = activation_from_bundle(
            bundle,
            identity,
            context,
            support=EvidencePolarity.SUPPORT,
        )

        selected_observation = {
            "constraint_id": activation.identity.constraint_id,
            "strength": activation.strength.value,
            "relevance": activation.strength.relevance,
            "freshness": activation.strength.freshness,
            "authority": activation.strength.authority,
            "support": activation.strength.support.value,
            "boundary_id": activation.context.boundary_id,
            "question": activation.context.question,
            "purpose": activation.context.purpose,
        }
        self.assertEqual(
            selected_observation,
            {
                "constraint_id": "bundle:c1",
                "strength": 0.72,
                "relevance": 0.81,
                "freshness": 0.63,
                "authority": 0.41,
                "support": "support",
                "boundary_id": "run:bounded-1",
                "question": "approval",
                "purpose": "bounded-equivalence",
            },
        )

    def test_enterprise_adapter_rejects_missing_observation_instead_of_zero(self):
        from types import SimpleNamespace
        from rdl_enterprise.constraint_adapter import activation_from_bundle

        identity = ConstraintIdentity("c-missing", "s", "rel", "o")
        context = BoundaryContext("b-missing")
        incomplete = SimpleNamespace(
            constraint_score=0.7,
            relevance=0.8,
            # freshness is intentionally absent
            authority_weight=0.4,
        )
        with self.assertRaises(ValueError):
            activation_from_bundle(incomplete, identity, context)

        explicit_unknown = SimpleNamespace(
            constraint_score=0.7,
            relevance=0.8,
            freshness=None,
            authority_weight=0.4,
        )
        with self.assertRaises(ValueError):
            activation_from_bundle(explicit_unknown, identity, context)
