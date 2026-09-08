"""
RDL Simulation Harness - Simulation Clock
仮想時刻・離散ステップ(Tick)および日時の進行を管理するモジュール。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class SimulationClock:
    """
    シミュレーション世界の離散時間クロック。
    Tick数および仮想日時 (ISO-8601) を同期進行させる。
    """
    current_tick: int = 0
    start_time: datetime = datetime(2026, 9, 1, 9, 0, 0)
    current_time: datetime = datetime(2026, 9, 1, 9, 0, 0)
    minutes_per_tick: int = 15

    def tick(self, ticks: int = 1) -> None:
        """指定した Tick 数だけ時間を進める"""
        if ticks < 0:
            raise ValueError("時間の巻き戻し(負のtick)は許可されていません")
        self.current_tick += ticks
        self.current_time += timedelta(minutes=self.minutes_per_tick * ticks)

    def advance_hours(self, hours: float) -> None:
        """指定時間だけ進める"""
        ticks = int((hours * 60) // self.minutes_per_tick)
        self.tick(max(1, ticks))

    def advance_days(self, days: int) -> None:
        """指定日数だけ進める"""
        ticks = int((days * 24 * 60) // self.minutes_per_tick)
        self.tick(ticks)

    @property
    def current_day(self) -> int:
        """開始日からの経過日数 (1-based: Day 1 が開始日)"""
        delta = self.current_time.date() - self.start_time.date()
        return delta.days + 1

    @property
    def iso_time(self) -> str:
        """現在日時の ISO-8601 文字列"""
        return self.current_time.isoformat()

    def __repr__(self) -> str:
        return f"<SimulationClock Day {self.current_day} (Tick {self.current_tick}) {self.iso_time}>"
