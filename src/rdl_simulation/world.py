from datetime import datetime
import random
from typing import Any, Callable, Dict, List, Optional
from rdl_simulation.clock import SimulationClock
from rdl_simulation.events import EventQueue, SimEvent, EventType
from rdl_simulation.agent import SimAgent, UserAgent, AuthorityAgent, EnvironmentAgent
from rdl_simulation.metrics import SimMetricsCollector
from rdl_simulation.replay import SimTraceLogger
from rdl_simulation.scenario import ScenarioPack, SimulationRunContext


class SimulationWorld:
    """
    RDLシミュレーション世界の実行統合体。
    """
    def __init__(
        self,
        start_day_str: str = "2026-09-01",
        start_hour: int = 9,
        start_minute: int = 0,
        minutes_per_tick: int = 15,
        seed: int = 42,
        rdl_adapter: Optional[Any] = None,
    ):
        start_date = datetime.fromisoformat(start_day_str).date()
        start_dt = datetime(start_date.year, start_date.month, start_date.day, start_hour, start_minute, 0)
        self.clock = SimulationClock(start_time=start_dt, minutes_per_tick=minutes_per_tick)
        self.seed = seed
        self.rng = random.Random(seed)
        self.event_queue = EventQueue()
        self.agents: Dict[str, SimAgent] = {}
        self.metrics = SimMetricsCollector()
        self.trace_logger = SimTraceLogger()
        self.rdl_adapter = rdl_adapter
        self.active_scenario: Optional[ScenarioPack] = None
        self.run_context: Optional[SimulationRunContext] = None
        self._custom_event_handlers: Dict[str, Callable[[SimEvent, "SimulationWorld"], None]] = {}

    def register_agent(self, agent: SimAgent) -> None:
        """エージェントを登録 (乱数シードをスコープ配布)"""
        agent_seed = self.rng.randint(0, 10**9)
        agent.set_rng(random.Random(agent_seed))
        self.agents[agent.agent_id] = agent

    def get_agent(self, agent_id: str) -> Optional[SimAgent]:
        return self.agents.get(agent_id)

    def register_event_handler(
        self,
        event_type: str,
        handler: Callable[[SimEvent, "SimulationWorld"], None],
    ) -> None:
        """カスタムイベントハンドラを登録"""
        self._custom_event_handlers[event_type] = handler

    def load_scenario(self, scenario: ScenarioPack) -> None:
        """シナリオパックを読み込み初期化"""
        self.active_scenario = scenario
        scenario.setup(self)

        initial_mb_hash = None
        if self.rdl_adapter and hasattr(self.rdl_adapter, "runtime"):
            initial_mb_hash = self.rdl_adapter.runtime.mb_graph.content_hash()

        import hashlib
        import json

        # エージェント設定ハッシュ
        agent_dicts = []
        for aid in sorted(self.agents.keys()):
            ag = self.agents[aid]
            a_info = {"id": ag.agent_id, "role": ag.role}
            if hasattr(ag, "persona"):
                p = ag.persona
                a_info["persona"] = {
                    "name": p.name,
                    "cohort": p.cohort,
                    "expertise": p.expertise,
                    "patience": p.patience,
                    "ambiguity": p.ambiguity,
                    "reliability": p.feedback_reliability,
                }
            agent_dicts.append(a_info)
        agents_hash = hashlib.sha256(json.dumps(agent_dicts, sort_keys=True).encode("utf-8")).hexdigest()[:16]

        # アダプター設定ハッシュ
        adapter_info = {}
        if self.rdl_adapter:
            adapter_info["type"] = self.rdl_adapter.__class__.__name__
            if hasattr(self.rdl_adapter, "oracle_answers"):
                adapter_info["oracle_answers"] = getattr(self.rdl_adapter, "oracle_answers", {})
            if hasattr(self.rdl_adapter, "timeout_interval_ticks"):
                adapter_info["timeout_interval_ticks"] = getattr(self.rdl_adapter, "timeout_interval_ticks", 16)
        adapter_hash = hashlib.sha256(json.dumps(adapter_info, sort_keys=True).encode("utf-8")).hexdigest()[:16]

        # ワールド設定ハッシュ (時計設定、登録カスタムイベントハンドラ名一覧等)
        world_info = {
            "clock_start": self.clock.start_time.isoformat(),
            "minutes_per_tick": self.clock.minutes_per_tick,
            "custom_handlers": sorted(list(self._custom_event_handlers.keys())),
        }
        world_config_hash = hashlib.sha256(json.dumps(world_info, sort_keys=True).encode("utf-8")).hexdigest()[:16]

        # ランタイム設定ハッシュ (力学定数 θ_0, γ, 耐久ハーネスチェッカー構成, 昇格ポリシー等)
        runtime_info = {}
        if self.rdl_adapter and hasattr(self.rdl_adapter, "runtime"):
            rt = self.rdl_adapter.runtime
            runtime_info["theta_0"] = getattr(rt.h_state, "theta_0", 2.0)
            runtime_info["gamma"] = getattr(rt.h_state, "gamma", 0.05)
            runtime_info["auto_promote"] = getattr(rt, "auto_promote_reorganizations", False)
            if hasattr(rt, "durability_harness") and rt.durability_harness:
                runtime_info["checkers"] = [
                    c.__class__.__name__ for c in getattr(rt.durability_harness, "checkers", [])
                ]
            if hasattr(rt, "default_promotion_policy") and rt.default_promotion_policy:
                pol = rt.default_promotion_policy
                runtime_info["default_policy"] = {
                    "durability": pol.require_durability,
                    "shadow": pol.require_shadow,
                    "human": pol.require_human_approval,
                    "min_cases": pol.min_resolved_cases,
                }
        runtime_config_hash = hashlib.sha256(json.dumps(runtime_info, sort_keys=True).encode("utf-8")).hexdigest()[:16]

        scenario_c_hash = scenario.content_hash() if hasattr(scenario, "content_hash") else "none"

        self.run_context = SimulationRunContext(
            seed=self.seed,
            clock_start_iso=self.clock.start_time.isoformat(),
            minutes_per_tick=self.clock.minutes_per_tick,
            scenario_name=scenario.name,
            scenario_version=getattr(scenario, "version", "v1.0"),
            scenario_content_hash=scenario_c_hash,
            agent_configs_hash=agents_hash,
            adapter_config_hash=adapter_hash,
            world_config_hash=world_config_hash,
            runtime_config_hash=runtime_config_hash,
            initial_mb_hash=initial_mb_hash,
        )

        # スケジュールされたシナリオイベントをイベントキューに登録
        for sev in scenario.scheduled_events:
            target_tick = self.clock.datetime_to_tick(
                day=sev.day,
                hour=sev.hour,
                minute=sev.minute,
            )

            self.event_queue.push(
                scheduled_tick=max(0, target_tick),
                event_type=sev.event_type,
                source_id=sev.source_id,
                target_id=sev.target_id,
                payload=sev.payload,
                priority=sev.priority,
            )

    def step(self) -> List[SimEvent]:
        """1 Tick 進め、発生したイベントを処理する"""
        self.clock.tick(1)
        curr_tick = self.clock.current_tick
        curr_day = self.clock.current_day

        # シナリオフック実行
        if self.active_scenario:
            self.active_scenario.on_tick(self, curr_tick, curr_day)

        # 実行可能イベントの取り出しとディスパッチ
        ready_events = self.event_queue.pop_ready(curr_tick)
        for ev in ready_events:
            self._dispatch_event(ev)

        # RDLランタイム側の定周期代謝 (タイムアウト処理・自然散逸等)
        if self.rdl_adapter and hasattr(self.rdl_adapter, "on_tick"):
            tick_digest_before = self.rdl_adapter.get_state_digest() if hasattr(self.rdl_adapter, "get_state_digest") else None
            self.rdl_adapter.on_tick(self, curr_tick, curr_day)
            tick_digest_after = self.rdl_adapter.get_state_digest() if hasattr(self.rdl_adapter, "get_state_digest") else None

            # 外来イベントなしに自律代謝によって状態変化が生じた場合、トレースへ記録 (因果律の完全捕捉)
            if tick_digest_before and tick_digest_after and tick_digest_before != tick_digest_after:
                self.trace_logger.record(
                    tick=curr_tick,
                    day=curr_day,
                    event_type="tick_metabolism",
                    source_id="clock",
                    target_id="runtime",
                    payload={"type": "autonomous_metabolism"},
                    transition_type="metabolic_dissipation_or_timeout",
                    state_digest_before=tick_digest_before,
                    state_digest_after=tick_digest_after,
                )

        # 日付境界でのメトリクススナップショット
        ticks_per_day = int((24 * 60) // self.clock.minutes_per_tick)
        if curr_tick % ticks_per_day == 0:
            heat = 0.0
            theta_eff = 0.0
            avg_k = 0.0
            inertia = 0.0
            if self.rdl_adapter and hasattr(self.rdl_adapter, "get_dynamics_state"):
                st = self.rdl_adapter.get_dynamics_state()
                heat = st.get("heat", 0.0)
                theta_eff = st.get("theta_eff", 0.0)
                avg_k = st.get("average_kappa", 0.0)
                inertia = st.get("total_inertia", 0.0)
            self.metrics.capture_daily_snapshot(
                day=curr_day,
                tick=curr_tick,
                heat_level=heat,
                theta_eff=theta_eff,
                average_kappa=avg_k,
                total_inertia=inertia,
            )

        return ready_events

    def _dispatch_event(self, ev: SimEvent) -> None:
        """イベント種別に応じたディスパッチ"""
        # ディスパッチ前の力学状態およびAIコア完全状態ダイジェストをキャプチャ
        mb_hash_before = None
        heat_before = None
        state_digest_before = None
        if self.rdl_adapter:
            if hasattr(self.rdl_adapter, "runtime"):
                rt = self.rdl_adapter.runtime
                mb_hash_before = rt.mb_graph.content_hash()
                heat_before = rt.h_state.version_total_heat(getattr(rt.mb_graph, "version", "prod"))
            if hasattr(self.rdl_adapter, "get_state_digest"):
                state_digest_before = self.rdl_adapter.get_state_digest()

        # 1. カスタムハンドラがあれば優先
        if ev.event_type in self._custom_event_handlers:
            self._custom_event_handlers[ev.event_type](ev, self)
        # 2. RDLランタイムアダプタへの転送
        elif self.rdl_adapter and hasattr(self.rdl_adapter, "handle_event"):
            self.rdl_adapter.handle_event(ev, self)

        # ディスパッチ後の力学状態およびAIコア完全状態ダイジェストをキャプチャ
        mb_hash_after = None
        heat_after = None
        transition_type = None
        state_digest_after = None
        if self.rdl_adapter:
            if hasattr(self.rdl_adapter, "runtime"):
                rt = self.rdl_adapter.runtime
                mb_hash_after = rt.mb_graph.content_hash()
                heat_after = rt.h_state.version_total_heat(getattr(rt.mb_graph, "version", "prod"))
                if mb_hash_before != mb_hash_after:
                    transition_type = "mb_update"
                elif heat_after != heat_before:
                    transition_type = "heat_change"
            if hasattr(self.rdl_adapter, "get_state_digest"):
                state_digest_after = self.rdl_adapter.get_state_digest()

        # トレースログ記録（因果前後の完全記録）
        self.trace_logger.record(
            tick=ev.scheduled_tick,
            day=self.clock.current_day,
            event_type=ev.event_type,
            source_id=ev.source_id,
            target_id=ev.target_id,
            payload=ev.payload,
            mb_hash_before=mb_hash_before,
            mb_hash_after=mb_hash_after,
            heat_before=heat_before,
            heat_after=heat_after,
            transition_type=transition_type,
            state_digest_before=state_digest_before,
            state_digest_after=state_digest_after,
        )

    def run_days(self, days: int) -> None:
        """指定日数分シミュレーションを進める"""
        ticks_per_day = int((24 * 60) // self.clock.minutes_per_tick)
        total_ticks = days * ticks_per_day
        for _ in range(total_ticks):
            self.step()

    def run_until_day(self, target_day: int) -> None:
        """指定した Day に到達するまでシミュレーションを進める"""
        ticks_per_day = int((24 * 60) // self.clock.minutes_per_tick)
        while self.clock.current_day < target_day:
            self.step()
