import unittest

from rdl_core import (
    AuthorityConstraint,
    BoundaryContext,
    CommitmentOrigin,
    CommitmentRecord,
    EvidencePolarity,
    Provenance,
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

    def test_core_source_has_no_runtime_package_imports(self):
        from pathlib import Path

        core_root = Path(__file__).parents[1] / "src" / "rdl_core"
        source = "\n".join(path.read_text(encoding="utf-8") for path in core_root.glob("*.py"))
        self.assertNotIn("rdl_enterprise", source)
        self.assertNotIn("rdl_simulation", source)
