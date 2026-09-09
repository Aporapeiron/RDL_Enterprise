"""
RDL Enterprise - Perturbation Stress Scenario
摂動・ノイズ・過酷条件下での力学的堅牢性検証シナリオ:
1. 表記揺れ (30%) の混入
2. 放置・無反応 (UNKNOWN化) の急増
3. 成功と失敗の交互連続注入 (熱蓄積と支持鮮度隔離の同時検証)
4. コホート別局所破断率 (初心者 vs 短気 vs 慎重) の比較
"""

from rdl_simulation.scenario import ScenarioPack
from rdl_simulation.world import SimulationWorld
from rdl_simulation.agent import Persona, UserAgent
from rdl_simulation.events import EventType


class PerturbationStressScenario(ScenarioPack):
    def __init__(self):
        super().__init__(
            name="perturbation_stress",
            description="高ノイズ・高放棄率・交互不整合による力学安定性および局所破断検出テスト",
        )

    def setup(self, world: SimulationWorld) -> None:
        # コホートの異なるユーザー群
        users = [
            UserAgent("user_imp_01", Persona("短気1", cohort="impatient", expertise=0.1, patience=0.0, ambiguity=0.6)),
            UserAgent("user_imp_02", Persona("短気2", cohort="impatient", expertise=0.2, patience=0.1, ambiguity=0.5)),
            UserAgent("user_new_01", Persona("新人1", cohort="newbie", expertise=0.2, patience=0.5, ambiguity=0.7)),
            UserAgent("user_care_01", Persona("慎重1", cohort="careful", expertise=0.7, patience=0.9, ambiguity=0.0)),
        ]
        for u in users:
            world.register_agent(u)

        # 30日間にわたり、過酷な摂動チケットを注入
        base_queries = [
            "パスワードリセットの手順を教えて",
            "パスワードの初期化をしたい",
            "パスワード忘れた",
            "ログインできないパスワード",
        ]

        # 偶数日は通常クエリ（成功期待）、奇数日は破断・未知クエリ（失敗・苦情期待）を明示交互注入
        for day in range(1, 31):
            for i, u in enumerate(users):
                q = base_queries[i % len(base_queries)]
                # 30% の確率で表記揺れノイズを付与
                if (day + i) % 3 == 0:
                    q = f"【至急】{q}！動かないです"

                query_data = u.create_query(q, category="account")

                # 奇数日は未知カテゴリーや誤作動を混ぜて失敗を誘発
                cat = "account" if day % 2 == 0 else "unknown_trouble"

                self.schedule_event(
                    day=day,
                    hour=10 + (i * 2),
                    minute=0,
                    event_type=EventType.USER_TICKET.value,
                    source_id=u.agent_id,
                    target_id="enterprise_ai",
                    payload={
                        "ticket_id": f"TICK-STRESS-D{day:02d}-{i}",
                        "query_text": query_data["query_text"],
                        "category": cat,
                        "cohort": u.persona.cohort,
                    },
                )
