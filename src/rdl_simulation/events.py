"""
RDL Simulation Harness - Event System
離散イベントおよび優先度付きイベントキューの管理モジュール。
"""

import heapq
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class EventType(str, Enum):
    # エージェントアクション
    USER_TICKET = "user_ticket"                   # 利用者がAIへ問い合わせ/要求を送信
    AI_RESPONSE = "ai_response"                   # AIランタイムが回答を出力
    USER_FEEDBACK = "user_feedback"               # 利用者が回答に対してフィードバック(解決/未解決/曖昧)を返却
    AUTHORITY_DIRECTIVE = "authority_directive"   # 権威者・先輩による方針指示・暗黙知注入
    
    # 環境・外部擾乱
    ENVIRONMENT_CHANGE = "environment_change"     # 制度変更、ツール移行、障害発生
    TIMEOUT_TRIGGER = "timeout_trigger"           # 放置案件のタイムアウトバッチ処理
    SCHEDULED_METRIC = "scheduled_metric"         # メトリクス収集・健全性レポート
    CUSTOM = "custom"


@dataclass(order=True)
class SimEvent:
    """
    シミュレーション世界で発生する離散イベント。
    (scheduled_tick, priority, sequence_id) で順序付けられる。
    """
    scheduled_tick: int
    priority: int = 10  # 値が小さいほど高優先度 (0が最高)
    sequence_id: int = 0  # 同一tick・priority時のFIFO保証用
    
    event_type: str = field(compare=False, default=EventType.USER_TICKET.value)
    source_id: str = field(compare=False, default="")
    target_id: str = field(compare=False, default="")
    payload: Dict[str, Any] = field(compare=False, default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"<SimEvent tick={self.scheduled_tick} type={self.event_type} "
            f"src={self.source_id}->tgt={self.target_id} prio={self.priority}>"
        )


class EventQueue:
    """
    時系列・優先度順のイベントキュー。
    heapq により O(log N) で効率的にイベントを管理。
    """
    def __init__(self):
        self._heap: List[SimEvent] = []
        self._seq_counter: int = 0

    def push(
        self,
        scheduled_tick: int,
        event_type: str,
        source_id: str,
        target_id: str,
        payload: Optional[Dict[str, Any]] = None,
        priority: int = 10,
    ) -> SimEvent:
        self._seq_counter += 1
        ev = SimEvent(
            scheduled_tick=scheduled_tick,
            priority=priority,
            sequence_id=self._seq_counter,
            event_type=event_type,
            source_id=source_id,
            target_id=target_id,
            payload=payload or {},
        )
        heapq.heappush(self._heap, ev)
        return ev

    def pop_ready(self, current_tick: int) -> List[SimEvent]:
        """current_tick 以前にスケジュールされているすべてのイベントを順に取り出す"""
        ready: List[SimEvent] = []
        while self._heap and self._heap[0].scheduled_tick <= current_tick:
            ready.append(heapq.heappop(self._heap))
        return ready

    def peek(self) -> Optional[SimEvent]:
        """次に予定されているイベントをプレビュー (取り出さない)"""
        return self._heap[0] if self._heap else None

    def __len__(self) -> int:
        return len(self._heap)

    def is_empty(self) -> bool:
        return len(self._heap) == 0
