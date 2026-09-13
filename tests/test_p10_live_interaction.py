"""Live P10 evidence: a real Jira observation followed by a later observation."""

import os
import json

import pytest

from rdl_enterprise import (
    AtlassianJiraConnector,
    AuthorityContext,
    BusinessInput,
    EnterpriseRuntime,
    FeedbackResult,
)
from rdl_enterprise.snapshot import RelationProvenance


LIVE = all(os.environ.get(name) for name in (
    "RDL_ATLASSIAN_BASE_URL",
    "RDL_ATLASSIAN_EMAIL",
    "RDL_ATLASSIAN_TOKEN",
))


@pytest.mark.skipif(not LIVE, reason="live Atlassian credentials are not configured")
def test_p10_live_jira_observation_to_subsequent_efp_and_h():
    connector = AtlassianJiraConnector.from_environment()
    issue_key = os.environ.get("RDL_ATLASSIAN_TEST_ISSUE", "IT-3")
    first = connector.lookup({"case_id": issue_key})

    runtime = EnterpriseRuntime()
    actor = AuthorityContext("p10-live-operator", "operator", "workflow", "human", "idp_sso")
    request = BusinessInput("P10-LIVE-IT3", "operator", "workflow", first["summary"])
    dispatched = runtime.dispatch_ticket(
        request,
        human_override_answer=json.dumps(first, ensure_ascii=False, sort_keys=True),
        authority=actor,
    )

    second = connector.lookup({"case_id": issue_key})
    feedback = FeedbackResult(
        user_resolved=False,
        actual_response_text=json.dumps(second, ensure_ascii=False, sort_keys=True),
        feedback_comment="subsequent live Jira observation",
        provenance=RelationProvenance(
            source_type="system",
            authority_level="unknown",
            source_id=f"atlassian-jira:{issue_key}",
            channel="standard",
            claim_type="fact",
            relation_type="current_state",
        ),
    )
    resolved = runtime.resolve_ticket_feedback(
        request.ticket_id, feedback, authority=actor
    )

    snapshot = runtime.resolved_snapshots[-1]
    assert dispatched.final_output == json.dumps(first, ensure_ascii=False, sort_keys=True)
    assert snapshot.efp_prime is not None
    assert snapshot.f_prime is not None
    assert snapshot.interaction_trace["pre_update_mb_hash"] == snapshot.frozen_context.frozen_mb.content_hash()
    assert resolved.e_prediction is not None
    assert resolved.e_input is not None
    assert "RDL_ATLASSIAN_TOKEN" not in repr(snapshot)
