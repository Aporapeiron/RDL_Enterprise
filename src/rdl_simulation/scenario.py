"""
RDL Simulation Harness - Scenario System
シナリオパック基底クラスおよび摂動注入インターフェース。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from rdl_simulation.world import SimulationWorld


@dataclass
class ScenarioEvent:
    day: int
    hour: int = 9
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
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.scheduled_events: List[ScenarioEvent] = []

    def schedule_event(
        self,
        day: int,
        hour: int,
        event_type: str,
        source_id: str,
        target_id: str,
        payload: Optional[Dict[str, Any]] = None,
        priority: int = 5,
    ) -> None:
        self.scheduled_events.append(
            ScenarioEvent(
                day=day,
                hour=hour,
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
