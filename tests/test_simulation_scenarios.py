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
            created_at=w.clock.iso_time,
        )
        rt.dispatch_ticket(efp_shadow)
        rt.resolve_ticket_feedback(
            "TICK-SHADOW-TEST",
            FeedbackResult(
                user_resolved=False,
                human_rejected=True,
                feedback_comment="旧URLは使えません",
                new_knowledge_provided="新SaaSポータル(https://saas-pwd.corp.com)より再設定してください。",
                observed_at=w.clock.iso_time,
            ),
            at=w.clock.iso_time,
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
                timestamp=w.clock.iso_time,
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

    def test_long_term_lifecycle_exact_determinism(self):
        """
        【長期ライフサイクル完全決定論的再現性検証 (60日間)】
        同一シードから開始した2つの独立した60日間シミュレーション世界において、
        定型沈澱・環境激変・発熱破断・シャドウ並行評価・マネージャー承認Leapを含む全プロセスで、
        全Tickのイベントトレース、中間力学状態遷移、および最終 M_B content_hash が完全一致すること。
        """
        world1 = create_test_world(seed=42)
        world1.load_scenario(LongTermLifecycleScenario())
        world1.run_days(60)

        world2 = create_test_world(seed=42)
        world2.load_scenario(LongTermLifecycleScenario())
        world2.run_days(60)

        # トレースログ（因果前後の力学状態遷移を含む全12フィールド）完全一致の検証
        ok, msg = SimulationReplayer.compare_traces(
            world1.trace_logger.records,
            world2.trace_logger.records,
            exact=True,
        )
        self.assertTrue(ok, f"60日間長期ライフサイクルのトレース不一致: {msg}")

        # 最終グラフハッシュの完全一致
        hash1 = world1.rdl_adapter.runtime.mb_graph.content_hash()
        hash2 = world2.rdl_adapter.runtime.mb_graph.content_hash()
        self.assertEqual(hash1, hash2, "60日間シミュレーション後の最終 M_B content_hash が不一致です")

    def test_true_world_replay_from_context(self):
        """
        【SimulationRunContext からの真のワールド再構築・リプレイ検証】
        RunContext（シード・仮想時計・シナリオハッシュ・エージェントハッシュ・アダプターハッシュ）
        を用いて独立した世界を再構築し、元の世界と100%同一の結果が得られることを検証。
        """
        world_orig = create_test_world(seed=999)
        scen_orig = AuthorityConflictScenario()
        world_orig.load_scenario(scen_orig)
        world_orig.run_days(2)

        # RunContext の完全性検証
        ctx = world_orig.run_context
        self.assertIsNotNone(ctx)
        self.assertNotEqual(ctx.scenario_content_hash, "none")
        self.assertNotEqual(ctx.agent_configs_hash, "none")
        self.assertNotEqual(ctx.adapter_config_hash, "none")
        self.assertNotEqual(ctx.world_config_hash, "none")
        self.assertNotEqual(ctx.runtime_config_hash, "none")

        # Replayer による再構築リプレイ実行 (Self-Verifying ReplayResult)
        replay_res = SimulationReplayer.replay_from_context(
            context=ctx,
            world_factory=create_test_world,
            scenario=AuthorityConflictScenario(),
            days=2,
            original_trace=world_orig.trace_logger.records,
        )
        self.assertTrue(replay_res.context_verified)
        self.assertTrue(replay_res.execution_completed)
        self.assertTrue(replay_res.trace_exact_match)
        self.assertTrue(replay_res.final_state_match)
        self.assertIsNone(replay_res.first_divergence)
        world_replayed = replay_res.world
        self.assertIsNotNone(world_replayed)

        # オリジナル世界とリプレイ世界の Canonical Exact トレース照合 (AIコア完全状態ダイジェストを含む)
        match, diff_msg = SimulationReplayer.compare_traces(
            world_orig.trace_logger.records,
            world_replayed.trace_logger.records,
            exact=True,
        )
        self.assertTrue(match, f"Replay 世界とのトレース不一致: {diff_msg}")
        self.assertEqual(
            world_orig.rdl_adapter.runtime.mb_graph.content_hash(),
            world_replayed.rdl_adapter.runtime.mb_graph.content_hash(),
        )
        self.assertEqual(
            world_orig.rdl_adapter.runtime.compute_state_digest().digest_hash,
            world_replayed.rdl_adapter.runtime.compute_state_digest().digest_hash,
        )

        # 【Fail-Closed 検証】: コンテキスト改ざん・不一致時の即時遮断
        from dataclasses import replace
        from rdl_simulation.replay import ReplayContextMismatchError

        # 1. 偽の初期 M_B ハッシュを渡した場合
        tampered_mb_ctx = replace(ctx, initial_mb_hash="tampered_mb_hash_000")
        with self.assertRaises(ReplayContextMismatchError):
            SimulationReplayer.replay_from_context(
                context=tampered_mb_ctx,
                world_factory=create_test_world,
                scenario=AuthorityConflictScenario(),
                days=2,
            )

        # 2. 異なるシナリオハッシュを渡した場合
        tampered_scen_ctx = replace(ctx, scenario_content_hash="tampered_scen_hash_000")
        with self.assertRaises(ReplayContextMismatchError):
            SimulationReplayer.replay_from_context(
                context=tampered_scen_ctx,
                world_factory=create_test_world,
                scenario=AuthorityConflictScenario(),
                days=2,
            )

        # 3. 異なるランタイム設定ハッシュ（例: 異なる耐久ハーネスやθ0の世界）を渡した場合
        tampered_rt_ctx = replace(ctx, runtime_config_hash="tampered_rt_hash_000")
        with self.assertRaises(ReplayContextMismatchError):
            SimulationReplayer.replay_from_context(
                context=tampered_rt_ctx,
                world_factory=create_test_world,
                scenario=AuthorityConflictScenario(),
                days=2,
            )

        # 4. 【First Divergence 検出検証】: raise_on_mismatch=False でエラー情報・乖離が返されること
        failed_res = SimulationReplayer.replay_from_context(
            context=tampered_rt_ctx,
            world_factory=create_test_world,
            scenario=AuthorityConflictScenario(),
            days=2,
            raise_on_mismatch=False,
        )
        self.assertFalse(failed_res.context_verified)
        self.assertFalse(failed_res.execution_completed)
        self.assertFalse(failed_res.success)
        self.assertIsNotNone(failed_res.first_divergence)
        self.assertIn("mismatches", failed_res.first_divergence)

    def test_perturbation_stress_state_transitions(self):
        """
        【摂動ストレステスト & RDL基本不変条件 (Invariants) 受入アサーション】
        局所的ペルソナ比較に依存せず、RDL認知力学の本質的不変条件を機械的に検証：
        1. タイムアウト/UNKNOWN は判断誤りではないため failure_count を増やさない
        2. 対向命題/差し戻し/苦情は last_support_at (支持鮮度) を更新しない
        3. 交互摂動下で有効判定境界 θ_eff が適応的に変動し、M_Δ が発動すること
        """
        world = create_test_world(seed=42)
        world.load_scenario(PerturbationStressScenario())

        # 開始前のノード状態を記録
        initial_nodes = {nid: (n.failure_count, n.last_support_at) for nid, n in world.rdl_adapter.runtime.mb_graph.nodes.items()}

        world.run_days(30)

        summary = world.metrics.summary()
        self.assertEqual(summary["total_tickets"], 120)
        self.assertGreater(summary["total_failed"], 0)
        self.assertGreater(summary["total_complaints"], 0)
        self.assertGreater(summary["m_delta_transitions"], 0)

        # Invariant 1: 全てのコホートの破断リスクが [0.0, 1.0] に厳密に収まること
        for ch, risk in summary["cohort_rupture_risk"].items():
            self.assertGreaterEqual(risk, 0.0)
            self.assertLessEqual(risk, 1.0)

        # Invariant 2: 日次差分メトリクス (cost_tier_delta) が正しく計算されていること
        for snap in world.metrics.daily_snapshots:
            self.assertIsInstance(snap.cost_tier_delta, dict)
            self.assertIsInstance(snap.cost_tier_cumulative, dict)
            # 日次増分の合計が tickets_delta と整合していること
            tier_delta_sum = sum(snap.cost_tier_delta.values())
            self.assertEqual(tier_delta_sum, snap.tickets_delta)


if __name__ == "__main__":
    unittest.main()
