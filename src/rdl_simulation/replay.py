"""
RDL Simulation Harness - Replay & Trace Logger
シミュレーションの決定論的トレースログ記録と反実仮想再生用モジュール。
"""

import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


@dataclass
class TraceRecord:
    tick: int
    day: int
    event_type: str
    source_id: str
    target_id: str
    payload: Dict[str, Any]
    result: Optional[Dict[str, Any]] = None


class SimTraceLogger:
    """
    全イベントの時系列トレースを記録し、再現性および監査可能性を担保する。
    """
    def __init__(self):
        self.records: List[TraceRecord] = []

    def record(
        self,
        tick: int,
        day: int,
        event_type: str,
        source_id: str,
        target_id: str,
        payload: Dict[str, Any],
        result: Optional[Dict[str, Any]] = None,
    ) -> TraceRecord:
        rec = TraceRecord(
            tick=tick,
            day=day,
            event_type=event_type,
            source_id=source_id,
            target_id=target_id,
            payload=payload,
            result=result,
        )
        self.records.append(rec)
        return rec

    def export_jsonl(self, filepath: str) -> None:
        """トレースログを JSON Lines 形式で書き出し"""
        with open(filepath, "w", encoding="utf-8") as f:
            for rec in self.records:
                f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")

    def __len__(self) -> int:
        return len(self.records)
