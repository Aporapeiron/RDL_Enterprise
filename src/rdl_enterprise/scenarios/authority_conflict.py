"""
RDL Enterprise - Authority Conflict Scenario
権威境界および管轄衝突の検証シナリオ:
1. 一般社員の伝聞・推量 (Description: 「〜らしい」) はコミットされず、支持証拠を持たないこと。
2. 管轄外マネージャー (例: 経理マネージャー) によるセキュリティ方針の越境介入が PermissionError で遮断されること。
3. 正統なセキュリティ責任者による指示 (Authority Commitment: 「〜とする」) のみが正しく反映され、出所が記録されること。
"""

from rdl_simulation.scenario import ScenarioPack
from rdl_simulation.world import SimulationWorld
from rdl_simulation.agent import Persona, UserAgent, AuthorityAgent
from rdl_simulation.events import EventType


class AuthorityConflictScenario(ScenarioPack):
    def __init__(self):
        super().__init__(
            name="authority_conflict",
            description="一般社員の伝聞(Description)と正統権威(Commitment)、および管轄外介入のフェイルクローズ遮断検証",
        )

    def setup(self, world: SimulationWorld) -> None:
        # 1. エージェント登録
        # (a) 一般社員
        user = UserAgent("user_tanaka", Persona("田中社員", cohort="general", expertise=0.3))
        world.register_agent(user)

        # (b) 管轄外マネージャー (経理管轄のみ)
        finance_mgr = AuthorityAgent(
            agent_id="mgr_finance",
            role="manager",
            domain_scopes=["finance"],  # security 管轄権を持たない
        )
        world.register_agent(finance_mgr)

        # (c) 正統セキュリティ責任者
        sec_admin = AuthorityAgent(
            agent_id="sec_suzuki",
            role="sec_admin",
            domain_scopes=["security"],
        )
        world.register_agent(sec_admin)

        # 2. イベントスケジューリング
        # Day 1, 10:00: 一般社員が「今後VPNは方式Xになるらしい」と問い合わせ
        self.schedule_event(
            day=1,
            hour=10,
            minute=0,
            event_type=EventType.USER_TICKET.value,
            source_id="user_tanaka",
            target_id="enterprise_ai",
            payload={
                "ticket_id": "TICK-RUMOR-01",
                "query_text": "今後VPN接続はプロキシ経由になるらしいですが設定を教えてください",
                "category": "security",
                "cohort": "general",
            },
        )

        # Day 1, 11:00: 経理マネージャーがセキュリティポリシーを変更しようと試行 (管轄外介入)
        self.schedule_event(
            day=1,
            hour=11,
            minute=0,
            event_type=EventType.AUTHORITY_DIRECTIVE.value,
            source_id="mgr_finance",
            target_id="enterprise_ai",
            payload={
                "verifier_id": "mgr_finance",
                "role": "manager",
                "domain": "security",
                "scope": "finance",
                "trigger_pattern": {"exact_keys": ["VPN新方式"]},
                "action_template": {"type": "direct_reply", "payload": "経理指示による新方式案内"},
            },
        )

        # Day 1, 14:00: 正統セキュリティ責任者が正式指示を注入
        self.schedule_event(
            day=1,
            hour=14,
            minute=0,
            event_type=EventType.AUTHORITY_DIRECTIVE.value,
            source_id="sec_suzuki",
            target_id="enterprise_ai",
            payload={
                "verifier_id": "sec_suzuki",
                "role": "admin",
                "domain": "security",
                "scope": "security",
                "trigger_pattern": {"exact_keys": ["VPN新方式"]},
                "action_template": {"type": "direct_reply", "payload": "セキュリティ部公認の新方式案内"},
            },
        )

