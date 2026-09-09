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

