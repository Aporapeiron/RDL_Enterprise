"""
RDL Cognitive Core Property-Based Invariant Tests
乱数シード・ペルソナ変動・イベント順序揺らぎ・タイムアウトノイズを大量生成し、
単一シナリオに依存しない RDL 認知力学の公理的不変条件 (Invariants) を機械的に検証する。
"""

import unittest
import sys
import os
import random
from typing import List, Dict, Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
from rdl_enterprise.runtime import EnterpriseRuntime
from rdl_enterprise.authority import AuthorityContext
from rdl_enterprise.durability import DurabilityHarness, RegressionHistoryChecker, AuthorityBoundaryChecker
from rdl_enterprise.simulation_adapter import EnterpriseSimAdapter
from rdl_enterprise.scenarios import LongTermLifecycleScenario, AuthorityConflictScenario, PerturbationStressScenario
from rdl_enterprise.snapshot import BusinessInput, FeedbackResult, CaseStatus
from rdl_simulation.world import SimulationWorld
from rdl_simulation.events import SimEvent, EventType
from rdl_simulation.agent import UserAgent, Persona
from rdl_simulation.replay import SimulationReplayer, ReplayContextMismatchError


def create_world_for_property(seed: int, theta_0: float = 2.0, gamma: float = 0.05) -> SimulationWorld:
    seed_path = os.path.join(os.path.dirname(__file__), "..", "data", "seed_it_support.json")
    graph = MBGraph.load_json(seed_path)

    durability_harness = DurabilityHarness(checkers=[
        RegressionHistoryChecker(),
        AuthorityBoundaryChecker(),
    ])

    runtime = EnterpriseRuntime(
        mb_graph=graph,
        theta_0=theta_0,
        gamma=gamma,
        durability_harness=durability_harness,
    )

    adapter = EnterpriseSimAdapter(
        runtime=runtime,
        oracle_answers={"account": "sso.corp.internal", "workflow": "旧ポータル"},
        timeout_interval_ticks=8,
    )

    world = SimulationWorld(
        start_day_str="2026-09-01",
        start_hour=9,
        start_minute=0,
        minutes_per_tick=15,
        seed=seed,
        rdl_adapter=adapter,
    )
    return world


class TestPropertyBasedInvariants(unittest.TestCase):
    """
    RDL認知力学の不変条件 (Invariants) に関する多変量プロパティベーステスト
    """

    def test_property_unknown_never_increments_failure_count_nor_refreshes_support(self):
        """
        【Property Invariant 1: UNKNOWN 意味的純粋性】
        任意のシード・入力において、放置・タイムアウト (mark_unknown) による不整合判定は
        推論誤りではないため failure_count を一切増加させず、かつ支持鮮度 (last_support_at) も更新しないこと。
        """
        for seed in [11, 22, 33, 44, 55]:
            world = create_world_for_property(seed=seed)
            rt = world.rdl_adapter.runtime

            # 初期ノード状態のサンプリング
            target_node_id = "node_pwd_reset"
            node = rt.mb_graph.get(target_node_id)
            self.assertIsNotNone(node)
            initial_fail = node.failure_count
            initial_support = node.last_support_at

            # 複数の案件を放置・タイムアウトさせる
            for i in range(5):
                tid = f"T_UNKNOWN_{seed}_{i}"
                efp = BusinessInput(
                    ticket_id=tid,
                    user_id=f"user_{seed}",
                    category="account",
                    query_text="パスワードリセット",
                    created_at=world.clock.iso_time,
                )
                rt.dispatch_ticket(efp)
                world.clock.tick(10)
                # タイムアウト失効 (mark_unknown)
                rt.expire_pending_tickets(ticket_ids=[tid], at=world.clock.iso_time)

            # 状態検査
            post_node = rt.mb_graph.get(target_node_id)
            self.assertEqual(
                post_node.failure_count,
                initial_fail,
                f"Seed {seed}: UNKNOWN により failure_count が不正増加しました: {post_node.failure_count} != {initial_fail}",
            )
            self.assertEqual(
                post_node.last_support_at,
                initial_support,
                f"Seed {seed}: UNKNOWN により last_support_at が更新されてしまいました",
            )

    def test_property_oppose_never_refreshes_support_freshness(self):
        """
        【Property Invariant 2: OPPOSE 不変性】
        差し戻し・苦情・対向フィードバック (human_rejected=True) は、
        如何なるシード・入力・時刻であっても支持鮮度 (last_support_at) を更新しないこと。
        """
        for seed in [101, 202, 303, 404, 505]:
            world = create_world_for_property(seed=seed)
            rt = world.rdl_adapter.runtime

            target_node_id = "node_workflow_app"
            node = rt.mb_graph.get(target_node_id)
            self.assertIsNotNone(node)
            initial_support = node.last_support_at
            initial_fail = node.failure_count

            for i in range(3):
                tid = f"T_OPPOSE_{seed}_{i}"
                world.clock.tick(20)
                curr_time = world.clock.iso_time

                efp = BusinessInput(
                    ticket_id=tid,
                    user_id=f"user_{seed}",
                    category="workflow",
                    query_text="稟議申請の承認ルート",
                    created_at=curr_time,
                )
                rt.dispatch_ticket(efp)

                # 差し戻しフィードバック注入
                rt.resolve_ticket_feedback(
                    tid,
                    FeedbackResult(
                        user_resolved=False,
                        human_rejected=True,
                        feedback_comment="間違いです",
                        observed_at=curr_time,
                    ),
                    at=curr_time,
                )

            post_node = rt.mb_graph.get(target_node_id)
            # last_support_at は一切更新されていないこと
            self.assertEqual(
                post_node.last_support_at,
                initial_support,
                f"Seed {seed}: OPPOSE フィードバックにより last_support_at が不正更新されました",
            )
            # failure_count は正しく増えていること
            self.assertGreater(
                post_node.failure_count,
                initial_fail,
                f"Seed {seed}: OPPOSE フィードバックで failure_count が増加していません",
            )

    def test_property_authority_fail_closed_under_randomized_intrusions(self):
        """
        【Property Invariant 3: 権威境界フェイルクローズの多変量不変性】
        ランダムに生成された非認可アクター（ロール不一致、スコープ外、認証欠落）による
        直接コミット試行は、100% 確実に遮断され、M_B 内にノードとして定着しないこと。
        """
        roles = ["intern", "contractor", "sales_rep", "guest", "auditor"]
        domains = ["security", "finance", "legal", "infrastructure", "hr"]

        for seed in range(10):
            rng = random.Random(seed)
            world = create_world_for_property(seed=seed)
            rt = world.rdl_adapter.runtime
            initial_count = len(rt.mb_graph.nodes)

            # ランダムな非認可アクターを生成
            for i in range(5):
                actor_id = f"unauthorized_actor_{seed}_{i}"
                role = rng.choice(roles)
                intruded_domain = rng.choice(domains)

                intruder_auth = AuthorityContext(
                    actor_id=actor_id,
                    role=role,
                    scope="general",  # intruded_domain と不一致
                    actor_type="human",
                    authenticated_by="basic_auth",
                    timestamp=world.clock.iso_time,
                )

                candidate_node = MBNode(
                    id=f"malicious_node_{seed}_{i}",
                    domain=intruded_domain,
                    trigger_pattern={"exact_keys": [f"trigger_{i}"]},
                    action_template={"type": "direct_reply", "payload": "exploit"},
                    confidence=0.9,
                )

                # 認可判定付きコミットを試行
                can_commit = intruder_auth.is_authorized_for(intruded_domain)
                self.assertFalse(can_commit, f"権限判定が漏洩しました: role={role}, scope=general, target={intruded_domain}")

                # フェイルクローズで拒絶されること
                try:
                    rt.mb_graph.commit_node(
                        candidate_node,
                        origin=CommitmentOrigin.AUTHORITY,
                        authority_context=intruder_auth,
                    )
                    self.fail(f"非認可コミットが拒絶されずに成功してしまいました: {actor_id}")
                except (PermissionError, ValueError, RuntimeError):
                    # 正常にフェイルクローズ遮断
                    pass

            # グラフに1つもノードが追加されていないこと
            self.assertEqual(len(rt.mb_graph.nodes), initial_count)

    def test_property_cohort_rupture_risk_strictly_bounded(self):
        """
        【Property Invariant 4: コホート破断リスク有界性】
        如何なる乱数シード、過酷な摂動（PerturbationStressScenario）であっても、
        全コホートの破断リスク (cohort_rupture_risk) は厳密に数学的閉区間 [0.0, 1.0] に収まること。
        """
        for seed in [1, 7, 42, 99, 555]:
            world = create_world_for_property(seed=seed)
            world.load_scenario(PerturbationStressScenario())
            world.run_days(10)  # 10日間摂動注入

            summary = world.metrics.summary()
            cohort_risks = summary.get("cohort_rupture_risk", {})
            self.assertGreater(len(cohort_risks), 0)

            for cohort_name, risk in cohort_risks.items():
                self.assertGreaterEqual(
                    risk, 0.0,
                    f"Seed {seed}, Cohort {cohort_name}: 破断リスクが 0.0 未満です ({risk})",
                )
                self.assertLessEqual(
                    risk, 1.0,
                    f"Seed {seed}, Cohort {cohort_name}: 破断リスクが 1.0 超過です ({risk})",
                )

    def test_property_same_run_context_always_exact_outcome(self):
        """
        【Property Invariant 5: RunContext 条件固定再現性】
        同一の RunContext からのシミュレーション再演は、中間トレース全フィールドおよび
        観測終了時 M_B content_hash が境界内同値の結末に収束すること。
        """
        for seed in [123, 456, 789]:
            world_a = create_world_for_property(seed=seed)
            scen_a = AuthorityConflictScenario()
            world_a.load_scenario(scen_a)
            world_a.run_days(2)

            ctx = world_a.run_context
            self.assertIsNotNone(ctx)

            # Replayer による同一世界再演 (ReplayResult 自律検証)
            replay_res = SimulationReplayer.replay_from_context(
                context=ctx,
                world_factory=create_world_for_property,
                scenario=AuthorityConflictScenario(),
                days=2,
                original_trace=world_a.trace_logger.records,
            )
            self.assertTrue(replay_res.success)
            self.assertTrue(replay_res.trace_exact_match)
            self.assertTrue(replay_res.final_state_match)
            self.assertIsNone(replay_res.first_divergence)

            world_b = replay_res.world
            self.assertEqual(
                world_a.rdl_adapter.runtime.mb_graph.content_hash(),
                world_b.rdl_adapter.runtime.mb_graph.content_hash(),
                f"Seed {seed}: 最終 M_B ハッシュが一致しません",
            )
            self.assertEqual(
                world_a.rdl_adapter.runtime.compute_state_digest().digest_hash,
                world_b.rdl_adapter.runtime.compute_state_digest().digest_hash,
                f"Seed {seed}: 最終 AI コア状態ダイジェストが一致しません",
            )

    def test_generative_fuzzed_query_and_interleaved_events_invariant(self):
        """
        【Generative Property Testing: ランダム生成クエリ & 任意イベント順序インターリーブ不変性】
        日本語・英数・記号・長文を含むランダム合成クエリと、乱数生成された到着順序（チケットとフィードバックの混在）
        において、RDLコアは如何なる入力ノイズに対しても例外破断（Crash）せず、かつ
        「成功していない案件は Level 0 や M_B に昇格沈澱しない」不変条件を厳格に保持すること。
        """
        sample_chars = "あいうえお漢字カタカナABCxyz012345!@#$%^&*()_+-=[]{}|;':,./<>? 　\n"
        rng = random.Random(777)

        for trial in range(5):
            world = create_world_for_property(seed=1000 + trial)
            rt = world.rdl_adapter.runtime
            initial_mb_hash = rt.mb_graph.content_hash()
            initial_l0_len = len(rt.cascade.level0_cache)

            # ランダムなイベント列を合成 (チケット投入、成功、差し戻し、放置の混在)
            events = []
            for i in range(15):
                # 乱数長クエリ生成
                q_len = rng.randint(1, 100)
                q_text = "".join(rng.choice(sample_chars) for _ in range(q_len))
                cat = rng.choice(["account", "network", "workflow", "security", "unknown_cat"])
                tid = f"FUZZ_{trial}_{i}"
                events.append(("dispatch", tid, cat, q_text))

                # 後続アクションを確率的決定
                action = rng.choice(["success", "reject", "timeout"])
                events.append((action, tid, None, None))

            # イベント順序をランダムシャッフル（ただし同一チケットのdispatchは先行）
            # チケットごとの順序制約を保ったシャッフル
            ticket_actions = {}
            for ev in events:
                ticket_actions.setdefault(ev[1], []).append(ev)

            interleaved_queue = []
            while any(ticket_actions.values()):
                active_tids = [tid for tid, acts in ticket_actions.items() if acts]
                chosen_tid = rng.choice(active_tids)
                interleaved_queue.append(ticket_actions[chosen_tid].pop(0))

            # インターリーブ実行
            for ev_type, tid, cat, text in interleaved_queue:
                world.clock.tick(5)
                cur_t = world.clock.iso_time
                if ev_type == "dispatch":
                    efp = BusinessInput(
                        ticket_id=tid,
                        user_id=f"fuzz_user_{trial}",
                        category=cat,
                        query_text=text,
                        created_at=cur_t,
                    )
                    res = rt.dispatch_ticket(efp)
                    self.assertIsNotNone(res)
                elif ev_type == "success":
                    if tid in rt.pending_snapshots:
                        rt.resolve_ticket_feedback(
                            tid,
                            FeedbackResult(user_resolved=True, observed_at=cur_t),
                            at=cur_t,
                        )
                elif ev_type == "reject":
                    if tid in rt.pending_snapshots:
                        rt.resolve_ticket_feedback(
                            tid,
                            FeedbackResult(user_resolved=False, human_rejected=True, observed_at=cur_t),
                            at=cur_t,
                        )
                elif ev_type == "timeout":
                    if tid in rt.pending_snapshots:
                        rt.expire_pending_tickets(ticket_ids=[tid], at=cur_t)

            # Property Invariant 検査:
            # 1. 遷移関連状態ダイジェストが矛盾なく算出可能であること
            digest = rt.compute_state_digest()
            self.assertIsNotNone(digest.digest_hash)
            self.assertEqual(len(digest.digest_hash), 16)

            # 2. 差し戻しやタイムアウトしたチケットが Level 0 キャッシュに混入していないこと (負の沈澱遮断)
            for snap in rt.resolved_snapshots:
                if snap.status in (CaseStatus.FAILURE, CaseStatus.REJECTED, CaseStatus.UNKNOWN):
                    key = (rt.mb_graph.version, snap.efp.category or "general", snap.efp.query_text)
                    self.assertNotIn(
                        key,
                        rt.cascade.level0_cache,
                        f"Seed {trial}: 失敗・差し戻し・タイムアウト案件 ({snap.status.value}, tid={snap.efp.ticket_id}) が Level 0 キャッシュに沈澱しています",
                    )
                    # 失敗案件が新規結晶化ルールとしてM_Bにコミットされていないこと
                    self.assertFalse(
                        snap.status == CaseStatus.REJECTED and getattr(snap, "is_authoritative", False),
                        "差し戻し案件が権威ノードとして誤認沈澱しています",
                    )


if __name__ == "__main__":
    unittest.main()
