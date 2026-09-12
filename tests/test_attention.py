from rdl_enterprise.attention import HumanAttentionGate
from rdl_enterprise.authority import AuthorityContext


def test_repeated_observations_aggregate_into_one_review():
    gate = HumanAttentionGate()
    actor = AuthorityContext("manager", "manager", "workflow", "human", "mfa")
    arguments = dict(case_id="IT-104", domain="workflow", change_point="blocked",
                     actor=actor, actionable=True, persistent=True)
    for _ in range(10):
        gate.consider(**arguments)
    assert gate.attention_load == 1
    assert gate.requests()[0].change_points == ("blocked",)
    gate.consider(**{**arguments, "change_point": "new-evidence"})
    assert gate.attention_load == 1
    assert gate.requests()[0].change_points == ("blocked", "new-evidence")


def test_review_requires_actionability_authority_and_persistence_or_safety():
    gate = HumanAttentionGate()
    actor = AuthorityContext("manager", "manager", "workflow", "human", "mfa")
    args = dict(case_id="IT-104", domain="workflow", change_point="blocked",
                actor=actor, actionable=True, persistent=True)
    assert gate.consider(**{**args, "actionable": False}) is None
    assert gate.consider(**{**args, "persistent": False}) is None
    wrong_actor = AuthorityContext("finance", "manager", "finance", "human", "mfa")
    assert gate.consider(**{**args, "actor": wrong_actor}) is None
    assert gate.attention_load == 0
    assert gate.consider(**{**args, "persistent": False, "safety_required": True})
