"""
RDL Enterprise - Long Term Lifecycle Scenario (60 Days)
長期時間軸における自律代謝循環の有限条件下検証シナリオ:
Day 1-15: 定型業務の反復とコスト沈澱 (Tier 0化・κ->0)
Day 16-30: 安定運用 (低コスト即答)
Day 31: 制度変更・ツール強制移行 (旧回答の無効化)
Day 32-35: 回答失敗・不整合熱の蓄積 (last_opposing_at上昇, H増加)
Day 36: Rupture (H >= theta_eff) -> M_Δ プロポーザル起草
Day 37-45: Shadow 並行推論と耐久検査
Day 46-55: Canary デプロイと隔離熱監視
Day 56: 正式昇格 (Leap) -> 新 M_B' 本番置換
Day 57-60: 新制度への適応完了・再沈澱
"""

from typing import Any, Dict, List, Optional
from rdl_simulation.scenario import ScenarioPack
from rdl_simulation.world import SimulationWorld
from rdl_simulation.agent import Persona, UserAgent, AuthorityAgent, EnvironmentAgent
from rdl_simulation.events import EventType


class LongTermLifecycleScenario(ScenarioPack):
    def __init__(self):
        super().__init__(
            name="long_term_lifecycle",
            description="60日間の長期ライフサイクルにおける完全代謝（沈澱・硬化・発熱・破断・再編・昇格・再沈澱）の検証",
        )

    def setup(self, world: SimulationWorld) -> None:
        # 1. 複数主体の登録
        # (a) 多様なペルソナを持つ利用者の登録
        users = [
            UserAgent("user_newbie", Persona("新人社員", cohort="newbie", expertise=0.2, patience=0.4, feedback_reliability=0.85, ambiguity=0.6)),
            UserAgent("user_veteran", Persona("ベテラン社員", cohort="veteran", expertise=0.8, patience=0.9, feedback_reliability=0.98, ambiguity=0.1)),
            UserAgent("user_impatient", Persona("短気な営業", cohort="impatient", expertise=0.3, patience=0.1, feedback_reliability=0.8, ambiguity=0.4)),
            UserAgent("user_careful", Persona("慎重な総務", cohort="careful", expertise=0.6, patience=0.8, feedback_reliability=0.95, ambiguity=0.1)),
        ]
        for u in users:
            world.register_agent(u)

        # (b) 権威者エージェントの登録
        tanaka_mgr = AuthorityAgent(
            agent_id="tanaka_mgr",
            role="manager",
            domain_scopes=["security", "workflow", "it_support"],
        )
        world.register_agent(tanaka_mgr)

        # (c) 環境エージェントの登録
        env = EnvironmentAgent("env_world")
        world.register_agent(env)

        # 初期世界で支持された解釈 (Oracle): 旧社内ポータルURL
        if world.rdl_adapter and hasattr(world.rdl_adapter, "oracle_answers"):
            world.rdl_adapter.oracle_answers["workflow"] = "旧申請ポータル"
            world.rdl_adapter.oracle_answers["account"] = "sso.corp.internal"

        # 2. 60日間のイベントスケジューリング
        # (Phase 1: Day 1〜15) 定型業務の反復 -> 沈澱
        queries_phase1 = [
            ("user_newbie", "パスワードリセットの方法を教えてください", "account"),
            ("user_veteran", "パスワードリセットの方法を教えてください", "account"),
            ("user_careful", "パスワードリセットの方法を教えてください", "account"),
        ]
        for day in range(1, 16):
            for i, (uid, qtext, cat) in enumerate(queries_phase1):
                self.schedule_event(
                    day=day,
                    hour=9 + (i * 2),
                    event_type=EventType.USER_TICKET.value,
                    source_id=uid,
                    target_id="enterprise_ai",
                    payload={"ticket_id": f"TICK-P1-D{day:02d}-{i}", "query_text": qtext, "category": cat, "cohort": world.get_agent(uid).persona.cohort},
                )

        # (Phase 2: Day 16〜30) 安定運用
        for day in range(16, 31, 2):
            self.schedule_event(
                day=day,
                hour=11,
                event_type=EventType.USER_TICKET.value,
                source_id="user_veteran",
                target_id="enterprise_ai",
                payload={"ticket_id": f"TICK-P2-D{day:02d}", "query_text": "パスワードリセットの方法を教えてください", "category": "account", "cohort": "veteran"},
            )

        # (Phase 3: Day 31) 環境変化: 会社が新SaaSへの完全移行を発表！
        self.schedule_event(
            day=31,
            hour=9,
            minute=0,
            event_type=EventType.ENVIRONMENT_CHANGE.value,
            source_id="env_world",
            target_id="world",
            payload={"change_type": "saas_migration", "category": "account", "new_oracle": "新SaaSポータル"},
            priority=1,
        )

        # (Phase 4: Day 32〜35) 旧回答による連続不整合・苦情発生 -> 発熱・Ruptureへ
        # AIは古い知識（旧ポータル）で即答するが、新SaaSになったため失敗・苦情となる
        troubled_queries = [
            ("user_impatient", "パスワードリセットの方法を教えてください", "account"),
            ("user_newbie", "パスワードリセットの方法を教えてください", "account"),
            ("user_careful", "パスワードリセットの方法を教えてください", "account"),
        ]
        for day in range(32, 36):
            for i, (uid, qtext, cat) in enumerate(troubled_queries):
                self.schedule_event(
                    day=day,
                    hour=10 + (i * 2),
                    minute=0,
                    event_type=EventType.USER_TICKET.value,
                    source_id=uid,
                    target_id="enterprise_ai",
                    payload={"ticket_id": f"TICK-FAIL-D{day:02d}-{i}", "query_text": qtext, "category": cat, "cohort": world.get_agent(uid).persona.cohort},
                )

        # (Phase 5-1: Day 37) シャドウ並行推論の開始
        self.schedule_event(
            day=37,
            hour=10,
            minute=0,
            event_type="start_shadow",
            source_id="tanaka_mgr",
            target_id="enterprise_ai",
            payload={"action": "enable_shadow"},
            priority=2,
        )

        # (Phase 5-2: Day 38) シャドウ並行評価用案件の流入
        self.schedule_event(
            day=38,
            hour=11,
            minute=0,
            event_type="shadow_eval_ticket",
            source_id="tanaka_mgr",
            target_id="enterprise_ai",
            payload={"action": "evaluate_shadow_triplet"},
            priority=2,
        )

        # (Phase 5-3: Day 40) マネージャーによる正式承認・Leap
        self.schedule_event(
            day=40,
            hour=14,
            minute=0,
            event_type="manager_promote",
            source_id="tanaka_mgr",
            target_id="enterprise_ai",
            payload={"action": "promote_latest_proposal"},
            priority=2,
        )

        # (Phase 6: Day 41〜60) 新制度での再適応・再沈澱
        for day in range(41, 61, 2):
            self.schedule_event(
                day=day,
                hour=10,
                minute=0,
                event_type=EventType.USER_TICKET.value,
                source_id="user_veteran",
                target_id="enterprise_ai",
                payload={"ticket_id": f"TICK-ADAPT-D{day:02d}", "query_text": "パスワードリセットの方法を教えてください", "category": "account", "cohort": "veteran"},
            )

    def on_tick(self, world: SimulationWorld, current_tick: int, current_day: int) -> None:
        pass
