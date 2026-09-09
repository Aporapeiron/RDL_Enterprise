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

    def __post_init__(self):
        if self.current_tick == 0:
            self.current_time = self.start_time

    def tick(self, ticks: int = 1) -> None:
        """指定した Tick 数だけ時間を進める"""
        if ticks < 0:
            raise ValueError("時間の巻き戻し(負のtick)は許可されていません")
        self.current_tick += ticks
        self.current_time = self.start_time + timedelta(minutes=self.minutes_per_tick * self.current_tick)

    def advance_hours(self, hours: float) -> None:
        """指定時間だけ進める"""
        ticks = int((hours * 60) // self.minutes_per_tick)
        self.tick(max(1, ticks))

    def advance_days(self, days: int) -> None:
        """指定日数だけ進める"""
        ticks = int((days * 24 * 60) // self.minutes_per_tick)
        self.tick(ticks)

    def datetime_to_tick(self, day: int, hour: int, minute: int = 0) -> int:
        """
        day (1-based), hour, minute から、start_time 起点の正確な tick 番号を算出する。
        例: start_time が 09:00 の場合、Day 1, 10:00 は (10:00 - 09:00) = 60分 -> tick 4。
        """
        if day < 1:
            raise ValueError(f"Day は 1 以上の整数を指定してください (指定値: {day})")
        start_date = self.start_time.date()
        target_date = start_date + timedelta(days=day - 1)
        target_dt = datetime(target_date.year, target_date.month, target_date.day, hour, minute, 0)

        delta = target_dt - self.start_time
        total_minutes = delta.total_seconds() / 60.0
        if total_minutes < 0:
            raise ValueError(f"指定時刻 {target_dt} はシミュレーション開始時刻 {self.start_time} より過去です")

        return int(total_minutes // self.minutes_per_tick)

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
