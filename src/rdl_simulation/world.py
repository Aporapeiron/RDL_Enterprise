"""
RDL Simulation Harness - Simulation World
クロック、イベントキュー、エージェント群、およびRDLランタイムを統合する中核モジュール。
"""

from typing import Any, Callable, Dict, List, Optional
from rdl_simulation.clock import SimulationClock
from rdl_simulation.events import EventQueue, SimEvent, EventType
from rdl_simulation.agent import SimAgent, UserAgent, AuthorityAgent, EnvironmentAgent
from rdl_simulation.metrics import SimMetricsCollector
from rdl_simulation.replay import SimTraceLogger
from rdl_simulation.scenario import ScenarioPack


class SimulationWorld:
    """
    RDLシミュレーション世界の実行統合体。
    """
    def __init__(
        self,
        start_day_str: str = "2026-09-01",
        minutes_per_tick: int = 15,
        rdl_adapter: Optional[Any] = None,
    ):
        self.clock = SimulationClock(minutes_per_tick=minutes_per_tick)
        self.event_queue = EventQueue()
        self.agents: Dict[str, SimAgent] = {}
        self.metrics = SimMetricsCollector()
        self.trace_logger = SimTraceLogger()
        self.rdl_adapter = rdl_adapter
        self.active_scenario: Optional[ScenarioPack] = None
        self._custom_event_handlers: Dict[str, Callable[[SimEvent, "SimulationWorld"], None]] = {}

    def register_agent(self, agent: SimAgent) -> None:
        """エージェントを登録"""
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

        # スケジュールされたシナリオイベントをイベントキューに登録
        for sev in scenario.scheduled_events:
            # day, hour から tick を計算
            day_offset = sev.day - 1
            hour_ticks = int((sev.hour * 60) // self.clock.minutes_per_tick)
            ticks_per_day = int((24 * 60) // self.clock.minutes_per_tick)
            target_tick = day_offset * ticks_per_day + hour_ticks

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
            self.rdl_adapter.on_tick(self, curr_tick, curr_day)

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
        # トレースログ記録
        self.trace_logger.record(
            tick=ev.scheduled_tick,
            day=self.clock.current_day,
            event_type=ev.event_type,
            source_id=ev.source_id,
            target_id=ev.target_id,
            payload=ev.payload,
        )

        # 1. カスタムハンドラがあれば優先
        if ev.event_type in self._custom_event_handlers:
            self._custom_event_handlers[ev.event_type](ev, self)
            return

        # 2. RDLランタイムアダプタへの転送
        if self.rdl_adapter and hasattr(self.rdl_adapter, "handle_event"):
            self.rdl_adapter.handle_event(ev, self)

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
