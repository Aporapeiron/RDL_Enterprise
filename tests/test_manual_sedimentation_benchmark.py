import json
import tempfile
import unittest
from pathlib import Path

import benchmark_manual_sedimentation as benchmark
from rdl_enterprise.mb_graph import MBGraph
from rdl_enterprise.runtime import EnterpriseRuntime
from rdl_enterprise.snapshot import BusinessInput, FeedbackResult


class TestManualSedimentationBenchmark(unittest.TestCase):
    def test_same_manual_and_case_set_are_used_without_label_leakage(self):
        with benchmark.CASES.open(encoding="utf-8") as handle:
            cases = json.load(handle)
        seed = MBGraph.load_json(str(benchmark.ROOT / "data" / "seed_it_support.json"))
        results = [benchmark.run_depth(seed, cases, depth) for depth in ("D1", "D2", "D3")]
        self.assertEqual([row["manual_source"] for row in results], ["data/manual_sedimentation/manual.md"] * 3)
        expected_case_ids = [case["case_id"] for case in cases]
        self.assertEqual([row["case_ids"] for row in results], [expected_case_ids] * 3)
        self.assertEqual([row["node_count"] for row in results], [5, 5, 5])
        self.assertEqual(results[0]["relation_count"], 0)
        self.assertGreater(results[1]["relation_count"], 0)
        self.assertGreater(results[2]["relation_count"], results[1]["relation_count"])
        self.assertTrue(all(0.0 <= row["behavior_match_rate"] <= 1.0 for row in results))
        self.assertEqual([row["runtime_status_match_rate"] for row in results], [1.0, 1.0, 1.0])
        self.assertTrue(all(row["baseline_human_override"] is False for row in results))
        self.assertTrue(all(row["expected_labels_in_runtime"] is False for row in results))
        self.assertTrue(all(row["policy_change_applied"] is True for row in results))
        self.assertTrue(all(row["policy_change_observed_status"] for row in results))
        self.assertTrue(all("runtime_status_counts" in row for row in results))
        self.assertTrue(all("unknown_rate" in row and "not_evaluated_rate" in row for row in results))

    def test_benchmark_does_not_persist_runtime_state(self):
        with benchmark.CASES.open(encoding="utf-8") as handle:
            cases = json.load(handle)
        seed = MBGraph.load_json(str(benchmark.ROOT / "data" / "seed_it_support.json"))
        before = seed.content_hash()
        with tempfile.TemporaryDirectory() as directory:
            benchmark.main(["--output-dir", directory])
        self.assertEqual(seed.content_hash(), before)

    def test_tier3_success_without_explicit_knowledge_does_not_crystallize(self):
        seed = MBGraph.load_json(str(benchmark.ROOT / "data" / "seed_it_support.json"))
        runtime = EnterpriseRuntime(mb_graph=benchmark.graph_for_depth(seed, "D1"))
        before = runtime.mb_graph.content_hash()
        runtime.handle_ticket(
            BusinessInput(
                ticket_id="NO-AUTO-001",
                user_id="synthetic",
                category="hardware",
                query_text="未知の設備障害を調査してほしい",
            ),
            feedback=FeedbackResult(user_resolved=True),
        )
        self.assertEqual(runtime.mb_graph.content_hash(), before)

    def test_fixed_seed_produces_repeatable_depth_metrics(self):
        with benchmark.CASES.open(encoding="utf-8") as handle:
            cases = json.load(handle)
        seed_a = MBGraph.load_json(str(benchmark.ROOT / "data" / "seed_it_support.json"))
        seed_b = MBGraph.load_json(str(benchmark.ROOT / "data" / "seed_it_support.json"))
        self.assertEqual(
            [benchmark.run_depth(seed_a, cases, d)["token_equivalent"] for d in ("D1", "D2", "D3")],
            [benchmark.run_depth(seed_b, cases, d)["token_equivalent"] for d in ("D1", "D2", "D3")],
        )


if __name__ == "__main__":
    unittest.main()
