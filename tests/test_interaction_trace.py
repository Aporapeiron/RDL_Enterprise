import tempfile
import copy
from pathlib import Path

from rdl_enterprise import BusinessInput, EnterpriseRuntime, FeedbackResult, AuthorityContext
from rdl_core import ConstraintIdentity, ConstraintStrength, EvidencePolarity, Provenance, RelationConstraintProfile


def test_actual_dispatch_response_trace_survives_restart():
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / "cases.sqlite3")
        runtime = EnterpriseRuntime(store_path=path)
        result = runtime.dispatch_ticket(
            BusinessInput("interaction-1", "operator", "workflow", "unknown request"),
            human_override_answer="Ask the requester for the affected service",
        )
        snapshot = runtime.pending_snapshots[result.ticket_id]
        trace = snapshot.interaction_trace
        assert trace["response"] == result.final_output
        assert trace["action_type"] == result.action_taken
        assert trace["pre_update_mb_hash"] == snapshot.frozen_context.frozen_mb.content_hash()
        assert trace["attribution"] == "POSSIBLE_ASSOCIATION"
        restored = EnterpriseRuntime(store_path=path).pending_snapshots[result.ticket_id]
        assert restored.interaction_trace == trace
        assert restored.e_prediction is None
        assert restored.efp_prime is None


def test_conflict_trace_links_feedback_without_adding_structural_heat():
    profiles = {
        name: RelationConstraintProfile(
            ConstraintIdentity(name, "server", "must_restart", "now", Provenance(name)),
            ConstraintStrength(0.9, polarity),
        )
        for name, polarity in (("security", EvidencePolarity.SUPPORT),
                               ("availability", EvidencePolarity.OPPOSE))
    }
    baseline = EnterpriseRuntime()
    inspected = EnterpriseRuntime(relation_profile_provider=lambda *_: profiles)
    request = BusinessInput("interaction-2", "operator", "workflow", "restart review")
    before = copy.deepcopy(inspected.h_state.__dict__)
    baseline.dispatch_ticket(request)
    dispatched = inspected.dispatch_ticket(request)
    assert dispatched.structural_conflict_count == 1
    assert inspected.h_state.__dict__ == before
    snapshot = inspected.pending_snapshots[request.ticket_id]
    trace = copy.deepcopy(snapshot.interaction_trace)
    assert trace["conflict_ids"]
    feedback = FeedbackResult(user_resolved=False, feedback_comment="Restart is still blocked")
    first = baseline.resolve_ticket_feedback(request.ticket_id, copy.deepcopy(feedback))
    second = inspected.resolve_ticket_feedback(request.ticket_id, feedback)
    assert second.e_prediction == first.e_prediction
    assert second.current_h == first.current_h
    assert snapshot.efp_prime == feedback
    assert snapshot.f_prime is not None
    assert snapshot.interaction_trace == trace
    assert snapshot.frozen_context.frozen_mb.content_hash() == trace["pre_update_mb_hash"]


def test_attention_gate_uses_only_actionable_persistent_feedback_and_deduplicates():
    actor = AuthorityContext("manager-1", "manager", "workflow", "human", "mfa")
    runtime = EnterpriseRuntime()
    request = BusinessInput(
        "attention-1", "operator", "workflow", "restart review",
        metadata={"interaction_series_id": "restart-series-1"},
    )
    runtime.dispatch_ticket(request, authority=actor)
    assert runtime.human_review_requests() == ()

    runtime.resolve_ticket_feedback(
        request.ticket_id,
        FeedbackResult(user_resolved=False, feedback_comment="still blocked"),
        authority=actor,
    )
    assert runtime.human_review_requests() == ()

    # A different case is not silently treated as persistence for this case.
    second = BusinessInput(
        "attention-2", "operator", "workflow", "restart review",
        metadata={"interaction_series_id": "restart-series-1"},
    )
    runtime.dispatch_ticket(second, authority=actor)
    runtime.resolve_ticket_feedback(
        second.ticket_id,
        FeedbackResult(user_resolved=False, feedback_comment="still blocked"),
        authority=actor,
    )
    assert len(runtime.human_review_requests()) == 1
    assert runtime.human_review_requests()[0].case_id == "restart-series-1"
