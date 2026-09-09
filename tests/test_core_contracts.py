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
    ConstraintIdentity,
    ConstraintStrength,
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
