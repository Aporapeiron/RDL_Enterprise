"""
RDL Simulation Scenarios Acceptance Tests
シナリオ実行が期待通りのRDL力学的状態遷移（沈澱・破断・発熱・Leap・権威境界・決定論的再現性）を
機械的に起こすことを証明する包括的受入テスト。
"""

import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from rdl_enterprise.mb_graph import MBGraph
from rdl_enterprise.runtime import EnterpriseRuntime
from rdl_enterprise.authority import AuthorityContext
from rdl_enterprise.durability import DurabilityHarness, RegressionHistoryChecker, AuthorityBoundaryChecker
from rdl_enterprise.simulation_adapter import EnterpriseSimAdapter
from rdl_enterprise.scenarios import LongTermLifecycleScenario, AuthorityConflictScenario, PerturbationStressScenario
from rdl_simulation.world import SimulationWorld
from rdl_simulation.events import SimEvent, EventType
from rdl_simulation.replay import SimulationReplayer


def create_test_world(seed: int = 42) -> SimulationWorld:
    seed_path = os.path.join(os.path.dirname(__file__), "..", "data", "seed_it_support.json")
    graph = MBGraph.load_json(seed_path)

    durability_harness = DurabilityHarness(checkers=[
        RegressionHistoryChecker(),
        AuthorityBoundaryChecker(),
    ])

    runtime = EnterpriseRuntime(
        mb_graph=graph,
        theta_0=2.0,
        gamma=0.05,
        durability_harness=durability_harness,
    )

    adapter = EnterpriseSimAdapter(
        runtime=runtime,
        oracle_answers={"account": "sso.corp.internal", "workflow": "旧ポータル"},
    )

    world = SimulationWorld(
        start_day_str="2026-09-01",
        start_hour=9,
        start_minute=0,
        minutes_per_tick=15,
        seed=seed,
        rdl_adapter=adapter,
    )

    # 環境変化ハンドラ
    def handle_env_change(ev: SimEvent, w: SimulationWorld):
        payload = ev.payload
        cat = payload.get("category")
        new_oracle = payload.get("new_oracle")
        if cat and new_oracle and hasattr(w.rdl_adapter, "oracle_answers"):
            w.rdl_adapter.oracle_answers[cat] = new_oracle

    world.register_event_handler(EventType.ENVIRONMENT_CHANGE.value, handle_env_change)

    # シャドウ並行推論開始ハンドラ
    def handle_start_shadow(ev: SimEvent, w: SimulationWorld):
        rt = w.rdl_adapter.runtime
        if rt.pending_reorganizations:
            latest_prop_id = list(rt.pending_reorganizations.keys())[-1]
            prop = rt.pending_reorganizations[latest_prop_id]
            # 候補ノードを新SaaS対応に更新
            cand_node = prop.candidate_mb.get(prop.hot_node_id)
            if cand_node:
                was_frozen = cand_node.is_frozen
                if was_frozen:
                    cand_node.unfreeze()
                cand_node.action_template = {
                    "type": "direct_reply",
                    "payload": "新SaaSポータル(https://saas-pwd.corp.com)より本人認証を行って再設定してください。",
                }
                if was_frozen:
                    cand_node.freeze()
            rt.enable_shadow_mode(latest_prop_id)

    world.register_event_handler("start_shadow", handle_start_shadow)

    # シャドウ評価案件流入ハンドラ
    def handle_shadow_eval(ev: SimEvent, w: SimulationWorld):
        rt = w.rdl_adapter.runtime
        from rdl_enterprise.snapshot import BusinessInput, FeedbackResult
        efp_shadow = BusinessInput(
            ticket_id="TICK-SHADOW-TEST",
            user_id="user_shadow",
            category="account",
            query_text="パスワードリセットの方法を教えてください",
        )
        rt.dispatch_ticket(efp_shadow)
        rt.resolve_ticket_feedback(
            "TICK-SHADOW-TEST",
            FeedbackResult(
                user_resolved=False,
                human_rejected=True,
                feedback_comment="旧URLは使えません",
                new_knowledge_provided="新SaaSポータル(https://saas-pwd.corp.com)より再設定してください。",
            ),
        )

    world.register_event_handler("shadow_eval_ticket", handle_shadow_eval)

    # マネージャー承認ハンドラ
    def handle_manager_promote(ev: SimEvent, w: SimulationWorld):
        rt = w.rdl_adapter.runtime
        if rt.pending_reorganizations:
            prop_id = list(rt.pending_reorganizations.keys())[-1]
            mgr_auth = AuthorityContext(
                actor_id="tanaka_mgr",
                role="manager",
                scope="all",
                actor_type="human",
                authenticated_by="idp_sso",
            )
            try:
                success = rt.promote_candidate_mb(prop_id, authority=mgr_auth)
                if success:
                    w.metrics.record_promotion()
            except Exception:
                pass

    world.register_event_handler("manager_promote", handle_manager_promote)

    return world


class TestSimulationScenariosAcceptance(unittest.TestCase):
    def test_simulation_exact_determinism(self):
        """
        【決定論的シミュレーション検証】
        同一の seed と初期状態から実行された2つのシミュレーション世界は、
        全Tickのイベントトレースおよび最終 M_B content_hash が完全一致すること。
        """
        world1 = create_test_world(seed=12345)
        world1.load_scenario(AuthorityConflictScenario())
        world1.run_days(2)

        world2 = create_test_world(seed=12345)
        world2.load_scenario(AuthorityConflictScenario())
        world2.run_days(2)

        # トレースログ完全一致の検証
        ok, msg = SimulationReplayer.compare_traces(
            world1.trace_logger.records,
            world2.trace_logger.records,
        )
        self.assertTrue(ok, f"決定論的トレース不一致: {msg}")

        # 最終グラフハッシュの完全一致
        hash1 = world1.rdl_adapter.runtime.mb_graph.content_hash()
        hash2 = world2.rdl_adapter.runtime.mb_graph.content_hash()
        self.assertEqual(hash1, hash2)

    def test_authority_conflict_state_transitions(self):
        """
        【権威管轄衝突シナリオ受入アサーション】
        1. 一般社員の伝聞問い合わせでは正統な支持証拠が捏造されないこと。
        2. 経理マネージャーによるセキュリティ方針の越境介入が遮断されること。
        3. 正統セキュリティ責任者の指示のみがコミットされ、来歴が保存されること。
        """
        world = create_test_world(seed=42)
        world.load_scenario(AuthorityConflictScenario())

        initial_node_count = len(world.rdl_adapter.runtime.mb_graph.nodes)

        # 2日間シミュレーション実行
        world.run_days(2)

        rt = world.rdl_adapter.runtime
        graph = rt.mb_graph

        # 正式セキュリティ責任者のノードが1件正統コミットされて増えていること
        self.assertEqual(len(graph.nodes), initial_node_count + 1)

        # 注入されたノードの特定と来歴検証
        injected_node = None
        for n in graph.nodes.values():
            if "VPN新方式" in n.trigger_pattern.get("exact_keys", []):
                injected_node = n
                break

        self.assertIsNotNone(injected_node)
        self.assertTrue(injected_node.is_committed)
        self.assertEqual(injected_node.commitment_origin, "authority")
        self.assertEqual(injected_node.commitment_record["actor"], "sec_suzuki")
        self.assertIn("security", injected_node.domain)

        # 経理マネージャーの介入によるノードは存在しないこと
        for n in graph.nodes.values():
            if n.commitment_record and n.commitment_record.get("actor") == "mgr_finance":
                self.fail("管轄外マネージャー (mgr_finance) によるノードが不正にコミットされています")

    def test_long_term_lifecycle_state_transitions(self):
        """
        【長期ライフサイクルシナリオ受入アサーション】
        Day 1-15: 定型反復による低コスト沈澱 (Tier 0化)
        Day 31: 環境変化
        Day 32-35: 失敗・苦情蓄積 -> H上昇 -> M_Δ 起草
        Day 40: マネージャー承認 -> 新バージョン本番反映 (Leap)
        Day 50-60: 新ルールによる再適応
        """
        world = create_test_world(seed=42)
        world.load_scenario(LongTermLifecycleScenario())
        rt = world.rdl_adapter.runtime

        initial_ver = rt.mb_graph.version

        # 1. Day 1〜30: 安定沈澱期
        world.run_until_day(31)

        # Day 30 時点で、チケットの大半が Tier 0 / Tier 1 で低コスト解決されていること
        tier_counts = world.metrics.cost_tier_counts
        total_p1 = tier_counts[0] + tier_counts[1]
        self.assertGreater(total_p1, 20)
        self.assertEqual(world.metrics.promotions_count, 0)
        self.assertEqual(rt.mb_graph.version, initial_ver)

        # 2. Day 31: 環境変化発火 -> Day 35 まで進行
        world.run_until_day(36)

        # 失敗・苦情により熱が蓄積し、再編相 M_Δ プロポーザルが起草されていること
        self.assertGreater(world.metrics.total_failed, 0)
        self.assertGreater(len(rt.pending_reorganizations), 0)
        self.assertGreater(world.metrics.m_delta_transitions, 0)

        # 3. Day 40 マネージャー承認を経て Day 45 まで進行
        world.run_until_day(45)

        # 昇格 (Leap) が発生し、本番グラフバージョンが更新されていること
        self.assertEqual(world.metrics.promotions_count, 1)
        self.assertNotEqual(rt.mb_graph.version, initial_ver)
        self.assertIn("cand-prop", rt.mb_graph.version)

        # 4. Day 60 まで完走
        world.run_until_day(61)

        # 最終サマリーにおいて、ユニーク破断率が [0.0, 1.0] に収まり、二重カウントがないこと
        summary = world.metrics.summary()
        for ch, risk in summary["cohort_rupture_risk"].items():
            self.assertGreaterEqual(risk, 0.0)
            self.assertLessEqual(risk, 1.0)

    def test_perturbation_stress_state_transitions(self):
        """
        【摂動ストレステスト受入アサーション】
        高ノイズ・未知クエリ混入下で破断・苦情が適切に検出され、
        慎重コホート等の局所破断リスクがメトリクスに正しく反映されること。
        """
        world = create_test_world(seed=42)
        world.load_scenario(PerturbationStressScenario())

        world.run_days(30)

        summary = world.metrics.summary()
        self.assertEqual(summary["total_tickets"], 120)
        self.assertGreater(summary["total_failed"], 0)
        self.assertGreater(summary["total_complaints"], 0)
        self.assertGreater(summary["m_delta_transitions"], 0)

        # 慎重コホート (careful) の破断リスクが他コホートよりも高く検出されていること
        careful_risk = summary["cohort_rupture_risk"].get("careful", 0.0)
        impatient_risk = summary["cohort_rupture_risk"].get("impatient", 0.0)
        self.assertGreater(careful_risk, impatient_risk)


if __name__ == "__main__":
    unittest.main()
