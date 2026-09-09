"""
RDL Simulation Harness - Replay & Trace Logger
シミュレーションの決定論的トレースログ記録と反実仮想再生用モジュール。
"""

import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class TraceRecord:
    tick: int
    day: int
    event_type: str
    source_id: str
    target_id: str
    payload: Dict[str, Any]
    result: Optional[Dict[str, Any]] = None
    mb_hash_before: Optional[str] = None
    mb_hash_after: Optional[str] = None
    heat_before: Optional[float] = None
    heat_after: Optional[float] = None
    transition_type: Optional[str] = None


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
        mb_hash_before: Optional[str] = None,
        mb_hash_after: Optional[str] = None,
        heat_before: Optional[float] = None,
        heat_after: Optional[float] = None,
        transition_type: Optional[str] = None,
    ) -> TraceRecord:
        rec = TraceRecord(
            tick=tick,
            day=day,
            event_type=event_type,
            source_id=source_id,
            target_id=target_id,
            payload=payload,
            result=result,
            mb_hash_before=mb_hash_before,
            mb_hash_after=mb_hash_after,
            heat_before=heat_before,
            heat_after=heat_after,
            transition_type=transition_type,
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


class SimulationReplayer:
    """
    保存されたトレースおよび RunContext を用いてシミュレーションを再演し、
    完全な決定論的一致（Exact Determinism）を検証するリプレイヤー。
    """
    @staticmethod
    def compare_traces(
        original: List[TraceRecord],
        replayed: List[TraceRecord],
        ignore_keys: Optional[List[str]] = None,
    ) -> Tuple[bool, Optional[str]]:
        """2つのトレースレコード群を逐次照合"""
        if len(original) != len(replayed):
            return False, f"トレースレコード長不一致: original={len(original)} vs replayed={len(replayed)}"

        ignore = set(ignore_keys or [])
        for i, (orig, rep) in enumerate(zip(original, replayed)):
            if orig.tick != rep.tick:
                return False, f"Record[{i}] Tick 不一致: orig={orig.tick} vs rep={rep.tick}"
            if orig.event_type != rep.event_type:
                return False, f"Record[{i}] EventType 不一致: orig={orig.event_type} vs rep={rep.event_type}"
            if orig.source_id != rep.source_id:
                return False, f"Record[{i}] SourceID 不一致: orig={orig.source_id} vs rep={rep.source_id}"

            # payload 照合 (ignore_keys 除外)
            orig_p = {k: v for k, v in orig.payload.items() if k not in ignore}
            rep_p = {k: v for k, v in rep.payload.items() if k not in ignore}
            if orig_p != rep_p:
                return False, f"Record[{i}] Payload 不一致: orig={orig_p} vs rep={rep_p}"

            # 力学状態遷移 (mb_hash / heat) の一致照合
            if orig.mb_hash_after != rep.mb_hash_after:
                return False, f"Record[{i}] mb_hash_after 不一致: orig={orig.mb_hash_after} vs rep={rep.mb_hash_after}"
            if orig.heat_after is not None and rep.heat_after is not None:
                if abs(orig.heat_after - rep.heat_after) > 1e-4:
                    return False, f"Record[{i}] heat_after 不一致: orig={orig.heat_after} vs rep={rep.heat_after}"

        return True, None

    @classmethod
    def replay_from_context(
        cls,
        context: Any,
        world_factory: Any,
        scenario: Any,
        days: int,
    ) -> Tuple[bool, Any, Optional[str]]:
        """
        SimulationRunContext を外生条件として受け取り、
        world_factory から独立した世界を再構築して完全に再演(Replay)する。
        """
        replayed_world = world_factory(seed=context.seed)
        replayed_world.load_scenario(scenario)
        replayed_world.run_days(days)
        return True, replayed_world, None
