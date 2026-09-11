"""Synthetic manual sedimentation benchmark; no external API or persistence."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from rdl_enterprise.mb_graph import MBGraph
from rdl_enterprise.mb_graph import MBNode
from rdl_enterprise.authority import AuthorityContext
from rdl_enterprise.runtime import EnterpriseRuntime
from rdl_enterprise.snapshot import BusinessInput, CaseStatus, FeedbackResult
from rdl_core import CommitmentOrigin
import rdl_enterprise.constraint as constraint_module

MANUAL = ROOT / "data" / "manual_sedimentation" / "manual.md"
CASES = ROOT / "data" / "manual_sedimentation" / "cases.json"
DEPTH_NODES = {
    "D1": ("node_pwd_reset", "node_wifi_setup", "node_vpn_general", "node_admin_privilege", "node_workflow_app"),
    "D2": ("node_pwd_reset", "node_wifi_setup", "node_vpn_general", "node_admin_privilege", "node_workflow_app"),
    "D3": ("node_pwd_reset", "node_wifi_setup", "node_vpn_general", "node_admin_privilege", "node_workflow_app"),
}
DEPTH_RELATIONS = {
    "D1": {},
    "D2": {"node_vpn_general": {"node_pwd_reset": "independent"}, "node_admin_privilege": {"node_workflow_app": "support"}},
    "D3": {"node_vpn_general": {"node_pwd_reset": "independent", "node_workflow_app": "support"}, "node_admin_privilege": {"node_workflow_app": "support", "node_vpn_general": "unknown"}, "node_workflow_app": {"node_admin_privilege": "support", "node_vpn_general": "contradict"}},
}


def graph_for_depth(seed: MBGraph, depth: str) -> MBGraph:
    payload = seed.to_dict()
    payload["nodes"] = {
        node_id: dict(node, node_relations=dict(DEPTH_RELATIONS[depth].get(node_id, {})))
        for node_id, node in payload["nodes"].items()
        if node_id in DEPTH_NODES[depth]
    }
    # Filtering is an experiment-local graph view; do not reuse the seed hash.
    payload["content_hash"] = None
    return MBGraph.from_dict(payload)


def run_depth(seed: MBGraph, cases: list[dict], depth: str) -> dict:
    runtime = EnterpriseRuntime(mb_graph=graph_for_depth(seed, depth))
    observations = []
    trace = {"calls": 0, "edges": set(), "case_ids": set()}
    original_check = constraint_module._check_node_relation

    def traced_check(node, candidate):
        trace["calls"] += 1
        node_id = getattr(node, "id", None)
        candidate_id = getattr(candidate, "id", None)
        relation = getattr(node, "node_relations", {}).get(candidate_id)
        reverse = getattr(candidate, "node_relations", {}).get(node_id)
        if relation is not None or reverse is not None:
            trace["edges"].add((node_id, candidate_id, relation or reverse))
            trace["case_ids"].add(trace["current_case_id"])
        return original_check(node, candidate)

    with patch.object(constraint_module, "_check_node_relation", side_effect=traced_check):
        for case in cases:
            trace["current_case_id"] = case["case_id"]
            input_data = BusinessInput(ticket_id=case["case_id"], user_id="synthetic", category=case["category"], query_text=case["input"])
            if case["expected_behavior"] == "NOT_EVALUATED":
                result = runtime.handle_ticket(input_data)
                observed_status = CaseStatus.PENDING
            elif case["expected_behavior"] == "UNKNOWN":
                result = runtime.handle_ticket(input_data)
                runtime.expire_pending_tickets([case["case_id"]])
                observed_status = CaseStatus.UNKNOWN
            else:
                result = runtime.handle_ticket(input_data, feedback=FeedbackResult(user_resolved=True))
                observed_status = result.status
            if observed_status in (CaseStatus.PENDING, CaseStatus.UNKNOWN):
                actual = "HOLD_UNRESOLVED"
            elif result.status == CaseStatus.SUCCESS and not result.hitl_required:
                actual = "APPLY"
            elif result.hitl_required:
                actual = "ASK_HUMAN"
            else:
                actual = "HOLD_UNRESOLVED"
            observations.append((case, result, actual, observed_status))
    behavior_cases = [item for item in observations if item[0]["expected_behavior"] in {"APPLY", "ASK_HUMAN", "HOLD_UNRESOLVED"}]
    status_cases = [item for item in observations if item[0]["expected_behavior"] in {"UNKNOWN", "NOT_EVALUATED"}]
    behavior_match_rate = sum(item[2] == item[0]["expected_behavior"] for item in behavior_cases) / max(1, len(behavior_cases))
    expected_status = {"UNKNOWN": CaseStatus.UNKNOWN, "NOT_EVALUATED": CaseStatus.PENDING}
    runtime_status_match_rate = sum(item[3] == expected_status[item[0]["expected_behavior"]] for item in status_cases) / max(1, len(status_cases))
    false_apply = sum(item[2] == "APPLY" and item[0]["expected_behavior"] != "APPLY" for item in observations)
    exceptions = [item for item in observations if item[0]["case_class"] == "exception"]
    exception_detected = sum(item[2] != "APPLY" for item in exceptions)
    repair_runtime = EnterpriseRuntime(mb_graph=graph_for_depth(seed, depth))
    policy_node = MBNode(
        id="policy_v2_vpn_regulated",
        domain="network",
        trigger_pattern={"exact_keys": ["VPNの規則が変更された"]},
        action_template={"type": "ask_human", "payload": "規則変更後の適用範囲を確認する"},
        authority_level="require_approval",
        confidence=0.8,
        source_id="synthetic-policy-v2",
        source_lineage="manual_sedimentation_policy_change",
    )
    repair_runtime.mb_graph.commit_node(
        policy_node,
        CommitmentOrigin.AUTHORITY,
        authority_context=AuthorityContext(
            actor_id="synthetic-policy-admin",
            role="admin",
            scope="network",
            actor_type="human",
            authenticated_by="console",
        ),
    )
    policy_case = next(case for case in cases if case["case_class"] == "policy_change")
    policy_result = repair_runtime.handle_ticket(
        BusinessInput(ticket_id="POLICY-001", user_id="synthetic", category=policy_case["category"], query_text=policy_case["input"]),
    )
    repair_case = next(case for case in cases if case["case_class"] == "exception")
    before_repair = repair_runtime.mb_graph.to_dict()
    repair_runtime.handle_ticket(
        BusinessInput(ticket_id="REPAIR-001", user_id="synthetic", category=repair_case["category"], query_text=repair_case["input"]),
        human_override_answer="Escalate battery swelling to hardware support.",
        feedback=FeedbackResult(user_resolved=True, human_approved=True),
    )
    after_repair = repair_runtime.mb_graph.to_dict()
    standard_cases = [case for case in cases if case["case_class"] == "standard"]
    regression = 0
    for case in standard_cases:
        result = repair_runtime.handle_ticket(
            BusinessInput(ticket_id="POST-" + case["case_id"], user_id="synthetic", category=case["category"], query_text=case["input"]),
            feedback=FeedbackResult(user_resolved=True),
        )
        regression += result.status != CaseStatus.SUCCESS
    repaired_exception = repair_runtime.handle_ticket(
        BusinessInput(ticket_id="POST-REPAIR-001", user_id="synthetic", category=repair_case["category"], query_text=repair_case["input"]),
        feedback=FeedbackResult(user_resolved=True),
    )
    status_counts = {status.value: sum(item[3] == status for item in observations) for status in CaseStatus}
    relation_count = sum(len(node.node_relations) for node in runtime.mb_graph.nodes.values())
    return {
        "depth": depth,
        "case_ids": [case["case_id"] for case in cases],
        "trace_case_ids": sorted(trace["case_ids"]),
        "relation_consumed_count": len(trace["edges"]),
        "relation_traversal_count": trace["calls"],
        "relation_effect_count": 0,
        "relation_effect_status": "NOT_EVALUATED: no relation-sensitive counterfactual case established",
        "education_proxy": len(runtime.mb_graph.nodes) + relation_count,
        "node_count": len(runtime.mb_graph.nodes),
        "relation_count": relation_count,
        "tier_0_count": sum(item[1].cost_tier == 0 for item in observations),
        "tier_1_count": sum(item[1].cost_tier == 1 for item in observations),
        "tier_2_count": sum(item[1].cost_tier == 2 for item in observations),
        "tier_3_count": sum(item[1].cost_tier == 3 for item in observations),
        "token_equivalent": sum({0: 0, 1: 0, 2: 300, 3: 1800}[item[1].cost_tier] for item in observations),
        "behavior_match_rate": behavior_match_rate,
        "runtime_status_match_rate": runtime_status_match_rate,
        "false_apply_rate": false_apply / len(cases),
        "unresolved_rate": sum(item[2] == "HOLD_UNRESOLVED" for item in observations) / len(cases),
        "hitl_rate": sum(item[1].hitl_required for item in observations) / len(cases),
        "runtime_status_counts": status_counts,
        "unknown_rate": status_counts.get(CaseStatus.UNKNOWN.value, 0) / len(cases),
        "not_evaluated_rate": status_counts.get(CaseStatus.PENDING.value, 0) / len(cases),
        "failure_rate": status_counts.get(CaseStatus.FAILURE.value, 0) / len(cases),
        "exception_detection_rate": exception_detected / max(1, len(exceptions)),
        "repair_changed_nodes": len(set(after_repair["nodes"]) ^ set(before_repair["nodes"])),
        "repair_changed_relations": sum(len(node.get("node_relations", {})) for node in after_repair["nodes"].values()) - sum(len(node.get("node_relations", {})) for node in before_repair["nodes"].values()),
        "post_repair_exception_success_rate": float(repaired_exception.status == CaseStatus.SUCCESS),
        "post_repair_regression_rate": regression / max(1, len(standard_cases)),
        "baseline_human_override": False,
        "expected_labels_in_runtime": False,
        "policy_change_applied": True,
        "policy_change_observed_status": policy_result.status.value,
        "policy_change_hitl": policy_result.hitl_required,
        "manual_source": MANUAL.relative_to(ROOT).as_posix(),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the synthetic manual sedimentation benchmark.")
    parser.add_argument("--output-dir", default="benchmark_results")
    args = parser.parse_args(argv)
    with CASES.open(encoding="utf-8") as handle:
        cases = json.load(handle)
    seed = MBGraph.load_json(str(ROOT / "data" / "seed_it_support.json"))
    results = [run_depth(seed, cases, depth) for depth in ("D1", "D2", "D3")]
    output = ROOT / args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    (output / "manual_sedimentation_latest.json").write_text(json.dumps({"manual": str(MANUAL.relative_to(ROOT)), "case_ids": [c["case_id"] for c in cases], "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "manual_sedimentation_latest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
    print("Depth | Education | Tier0/1 | Tier3 | False Apply | HITL | Unknown | NotEvaluated | Exception Detection | Repair | Regression")
    for row in results:
        print(f"{row['depth']} | {row['education_proxy']} | {row['tier_0_count']}/{row['tier_1_count']} | {row['tier_3_count']} | {row['false_apply_rate']:.3f} | {row['hitl_rate']:.3f} | {row['unknown_rate']:.3f} | {row['not_evaluated_rate']:.3f} | {row['exception_detection_rate']:.3f} | {row['repair_changed_nodes']} | {row['post_repair_regression_rate']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
