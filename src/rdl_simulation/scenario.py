"""
RDL Simulation Harness - Scenario System
シナリオパック基底クラスおよび摂動注入インターフェース。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from rdl_simulation.world import SimulationWorld


@dataclass(frozen=True)
class SimulationRunContext:
    """
    シミュレーション実行の外生固定条件コンテキスト (BASE v2.0 §4.2 ReplayToken 整合)。
    同一の RunContext からの実行は、現在の観測境界内で同一のイベント順序および結果を再現する。
    """
    seed: int
    clock_start_iso: str
    minutes_per_tick: int
    scenario_name: str
    scenario_version: str = "v1.0"
    scenario_content_hash: str = "none"
    agent_configs_hash: str = "none"
    adapter_config_hash: str = "none"
    world_config_hash: str = "none"
    runtime_config_hash: str = "none"
    initial_mb_hash: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class ScenarioEvent:
    day: int
    hour: int = 9
    minute: int = 0
    event_type: str = "custom"
    source_id: str = "scenario"
    target_id: str = "world"
    payload: Optional[Dict[str, Any]] = None
    priority: int = 5


class ScenarioPack(ABC):
    """
    シミュレーションシナリオの定義パック。
    長期ライフサイクル、権威管轄衝突、摂動ストレスなどをカプセル化する。
    """
    def __init__(self, name: str, description: str = "", version: str = "v1.0"):
        self.name = name
        self.description = description
        self.version = version
        self.scheduled_events: List[ScenarioEvent] = []

    def schedule_event(
        self,
        day: int,
        hour: int,
        minute: int = 0,
        event_type: str = "custom",
        source_id: str = "scenario",
        target_id: str = "world",
        payload: Optional[Dict[str, Any]] = None,
        priority: int = 5,
    ) -> None:
        self.scheduled_events.append(
            ScenarioEvent(
                day=day,
                hour=hour,
                minute=minute,
                event_type=event_type,
                source_id=source_id,
                target_id=target_id,
                payload=payload or {},
                priority=priority,
            )
        )

    @abstractmethod
    def setup(self, world: "SimulationWorld") -> None:
        """エージェントの登録や初期イベントの予約を行う"""
        pass

    def on_tick(self, world: "SimulationWorld", current_tick: int, current_day: int) -> None:
        """毎Tickの動的フック (必要に応じてオーバーライド)"""
        pass

    def content_hash(self) -> str:
        """シナリオの構成およびイベント群の条件固定再現用ハッシュ"""
        import hashlib
        import json
        ev_dicts = [
            {
                "day": ev.day,
                "hour": ev.hour,
                "minute": ev.minute,
                "event_type": ev.event_type,
                "source_id": ev.source_id,
                "target_id": ev.target_id,
                "payload": ev.payload,
                "priority": ev.priority,
            }
            for ev in self.scheduled_events
        ]
        data = {
            "name": self.name,
            "version": self.version,
            "events": ev_dicts,
        }
        serialized = json.dumps(data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
