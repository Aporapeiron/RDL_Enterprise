"""
RDL Multi-Agent Simulation Harness Runner
長期・複数主体・イベント駆動シミュレーションの実行スクリプト。

使用方法:
  py run_multiagent_sim.py --scenario lifecycle --days 60
  py run_multiagent_sim.py --scenario authority --days 2
  py run_multiagent_sim.py --scenario stress --days 15
"""

import sys
import os
import argparse
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "src")))

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from rdl_enterprise.mb_graph import MBGraph
from rdl_enterprise.runtime import EnterpriseRuntime
from rdl_enterprise.authority import AuthorityContext
from rdl_enterprise.durability import DurabilityHarness, RegressionHistoryChecker, AuthorityBoundaryChecker, PerturbationStressChecker
from rdl_enterprise.social_adapter import SocialFixtureAdapter
from rdl_enterprise.simulation_adapter import EnterpriseSimAdapter
from rdl_enterprise.scenarios import LongTermLifecycleScenario, AuthorityConflictScenario, PerturbationStressScenario
from rdl_simulation.world import SimulationWorld
from rdl_simulation.events import SimEvent, EventType


def print_banner(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def build_world(scenario_name: str) -> SimulationWorld:
    seed_path = os.path.join(os.path.dirname(__file__), "data", "seed_it_support.json")
    graph = MBGraph.load_json(seed_path)

    social_fixtures = SocialFixtureAdapter.load_from_json("data/social_fixtures_sample.json")
    durability_harness = DurabilityHarness(checkers=[
        RegressionHistoryChecker(),
        AuthorityBoundaryChecker(),
        PerturbationStressChecker(perturbation_fixtures=social_fixtures),
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

    world = SimulationWorld(minutes_per_tick=15, rdl_adapter=adapter)

    # 環境変化イベントハンドラ
    def handle_env_change(ev: SimEvent, w: SimulationWorld):
        payload = ev.payload
        cat = payload.get("category")
        new_oracle = payload.get("new_oracle")
        if cat and new_oracle and hasattr(w.rdl_adapter, "oracle_answers"):
            w.rdl_adapter.oracle_answers[cat] = new_oracle
            print(f"\n[環境激変] {cat} の制度・ツールが更新されました！ (新Oracle: {new_oracle})")

    world.register_event_handler(EventType.ENVIRONMENT_CHANGE.value, handle_env_change)

    # シャドウ並行推論開始ハンドラ
    def handle_start_shadow(ev: SimEvent, w: SimulationWorld):
        rt = w.rdl_adapter.runtime
        if rt.pending_reorganizations:
            latest_prop_id = list(rt.pending_reorganizations.keys())[-1]
            prop = rt.pending_reorganizations[latest_prop_id]
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
            print(f"\n[シャドウ開始] プロポーザル '{latest_prop_id}' の並行反実仮想評価を開始しました。")

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

    # 管理者承認ハンドラ
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
                    print(f"\n[マネージャー承認] プロポーザル '{prop_id}' が正式承認され、本番反映(Leap)されました！")
                    w.metrics.record_promotion()
                else:
                    print(f"\n[マネージャー承認失敗] 昇格ゲートを通過できませんでした。")
            except Exception as e:
                print(f"\n[マネージャー承認失敗] {e}")

    world.register_event_handler("manager_promote", handle_manager_promote)

    return world


def main():
    parser = argparse.ArgumentParser(description="RDL Multi-Agent Simulation Runner")
    parser.add_argument("--scenario", type=str, default="lifecycle", choices=["lifecycle", "authority", "stress"], help="実行シナリオ")
    parser.add_argument("--days", type=int, default=None, help="シミュレーション日数")
    args = parser.parse_args()

    print_banner(f"RDL Simulation Harness - Scenario: {args.scenario.upper()}")

    world = build_world(args.scenario)

    if args.scenario == "lifecycle":
        days = args.days or 60
        scenario = LongTermLifecycleScenario()
    elif args.scenario == "authority":
        days = args.days or 2
        scenario = AuthorityConflictScenario()
    elif args.scenario == "stress":
        days = args.days or 15
        scenario = PerturbationStressScenario()
    else:
        raise ValueError(f"未知のシナリオ: {args.scenario}")

    world.load_scenario(scenario)
    print(f"▶ シナリオ '{scenario.name}' をロードしました。")
    print(f"  登録エージェント数: {len(world.agents)} 名")
    print(f"  総スケジュールイベント数: {len(world.event_queue)} 件")
    print(f"  実行予定期間: Day 1 〜 Day {days} ({days * 24 * 4} Ticks)")

    # 進行ループ
    last_reported_day = 0
    ticks_per_day = 24 * 4
    total_ticks = days * ticks_per_day

    for t in range(1, total_ticks + 1):
        world.step()
        curr_day = world.clock.current_day
        if curr_day != last_reported_day and curr_day <= days:
            last_reported_day = curr_day
            st = world.rdl_adapter.get_dynamics_state()
            if curr_day in [1, 15, 30, 31, 35, 40, 50, 60] or curr_day == days:
                print(
                    f"  [Day {curr_day:02d}] Ticks: {world.clock.current_tick:04d} | "
                    f"Heat: {st['heat']:.2f} / θ_eff: {st['theta_eff']:.2f} | "
                    f"avg κ: {st['average_kappa']:.3f} | Total Inertia: {st['total_inertia']:.2f} | "
                    f"Graph Ver: {st['version']}"
                )

    print_banner("シミュレーション結果サマリー (RDL Simulation Metrics)")
    summary = world.metrics.summary()
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    print("\n[コホート別局所破断リスク評価]")
    for ch, risk in summary["cohort_rupture_risk"].items():
        print(f"  - Cohort '{ch}': 破断・不満率 = {risk * 100:.1f}%")

    print("\n✅ シミュレーションが正常に完了しました。")


if __name__ == "__main__":
    main()
