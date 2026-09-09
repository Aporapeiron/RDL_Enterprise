"""
RDL Simulation Harness - Metrics & Cohort Analysis
シミュレーション全体の力学メトリクスおよびコホート別局所破断・発熱の追跡モジュール。
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DailySnapshot:
    day: int
    tick: int
    tickets_delta: int = 0         # その日の新規チケット数
    resolved_delta: int = 0        # その日の解決数
    failed_delta: int = 0          # その日の失敗数
    unknown_delta: int = 0         # その日の未解決/タイムアウト数
    cumulative_tickets: int = 0    # 累計チケット数
    cumulative_resolved: int = 0   # 累計解決数
    cumulative_failed: int = 0     # 累計失敗数
    cumulative_unknown: int = 0    # 累計タイムアウト数
    cost_tier_today: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    heat_level: float = 0.0
    theta_eff: float = 0.0
    average_kappa: float = 0.0
    total_inertia: float = 0.0


class SimMetricsCollector:
    """
    シミュレーション全体の統計情報およびコホート別メトリクスを収集・集計する。
    二重カウントを排除し、厳密な自律解決率と局所破断率を提供する。
    """
    def __init__(self):
        self.total_tickets: int = 0
        self.total_resolved: int = 0
        self.total_failed: int = 0
        self.total_unknown: int = 0
        self.total_complaints: int = 0
        self.autonomous_resolved_count: int = 0
        self.hitl_count: int = 0
        self.m_delta_transitions: int = 0
        self.promotions_count: int = 0

        self.cost_tier_counts: Dict[int, int] = defaultdict(int)
        self._ticket_cost_tiers: Dict[str, int] = {}
        
        # コホート別チケット追跡 (二重カウント防止用ユニーク集合)
        self.cohort_tickets: Dict[str, set] = defaultdict(set)
        self.cohort_ruptured_tickets: Dict[str, set] = defaultdict(set)
        self.cohort_resolved_tickets: Dict[str, set] = defaultdict(set)

        # 前日スナップショット時点の累計値 (日次増分算出用)
        self._last_snap_tickets = 0
        self._last_snap_resolved = 0
        self._last_snap_failed = 0
        self._last_snap_unknown = 0

        # 日次推移
        self.daily_snapshots: List[DailySnapshot] = []

    def record_ticket(
        self,
        ticket_id: str,
        cost_tier: int,
        cohort: str = "general",
        hitl_triggered: bool = False,
    ) -> None:
        self.total_tickets += 1
        self.cost_tier_counts[cost_tier] += 1
        self._ticket_cost_tiers[ticket_id] = cost_tier
        self.cohort_tickets[cohort].add(ticket_id)
        if hitl_triggered:
            self.hitl_count += 1

    def record_feedback(
        self,
        ticket_id: str,
        cohort: str,
        user_resolved: Optional[bool],
        complaint: bool = False,
    ) -> None:
        cost_tier = self._ticket_cost_tiers.get(ticket_id, 3)

        if user_resolved is True:
            self.total_resolved += 1
            self.cohort_resolved_tickets[cohort].add(ticket_id)
            if cost_tier in (0, 1, 2):
                self.autonomous_resolved_count += 1
        elif user_resolved is False:
            self.total_failed += 1
            self.cohort_ruptured_tickets[cohort].add(ticket_id)
        else:
            self.total_unknown += 1
            self.cohort_ruptured_tickets[cohort].add(ticket_id)

        if complaint:
            self.total_complaints += 1
            self.cohort_ruptured_tickets[cohort].add(ticket_id)

    def record_m_delta_transition(self) -> None:
        self.m_delta_transitions += 1

    def record_promotion(self) -> None:
        self.promotions_count += 1

    def capture_daily_snapshot(
        self,
        day: int,
        tick: int,
        heat_level: float = 0.0,
        theta_eff: float = 0.0,
        average_kappa: float = 0.0,
        total_inertia: float = 0.0,
    ) -> DailySnapshot:
        tickets_delta = self.total_tickets - self._last_snap_tickets
        resolved_delta = self.total_resolved - self._last_snap_resolved
        failed_delta = self.total_failed - self._last_snap_failed
        unknown_delta = self.total_unknown - self._last_snap_unknown

        self._last_snap_tickets = self.total_tickets
        self._last_snap_resolved = self.total_resolved
        self._last_snap_failed = self.total_failed
        self._last_snap_unknown = self.total_unknown

        snap = DailySnapshot(
            day=day,
            tick=tick,
            tickets_delta=tickets_delta,
            resolved_delta=resolved_delta,
            failed_delta=failed_delta,
            unknown_delta=unknown_delta,
            cumulative_tickets=self.total_tickets,
            cumulative_resolved=self.total_resolved,
            cumulative_failed=self.total_failed,
            cumulative_unknown=self.total_unknown,
            cost_tier_today=dict(self.cost_tier_counts),
            heat_level=heat_level,
            theta_eff=theta_eff,
            average_kappa=average_kappa,
            total_inertia=total_inertia,
        )
        self.daily_snapshots.append(snap)
        return snap

    def summary(self) -> Dict[str, Any]:
        """サマリーレポートを生成"""
        total_finished = self.total_resolved + self.total_failed + self.total_unknown
        overall_rate = (self.total_resolved / total_finished) if total_finished > 0 else 0.0
        auto_rate = (self.autonomous_resolved_count / total_finished) if total_finished > 0 else 0.0
        hitl_rate = (self.hitl_count / self.total_tickets) if self.total_tickets > 0 else 0.0

        # コホート別破断率 (ユニーク破断チケット数 / コホート総チケット数: 必ず [0.0, 1.0])
        cohort_rupture_risk: Dict[str, float] = {}
        cohort_stats: Dict[str, Dict[str, int]] = {}
        for ch, tids in self.cohort_tickets.items():
            total_c = len(tids)
            ruptured_c = len(self.cohort_ruptured_tickets.get(ch, set()))
            resolved_c = len(self.cohort_resolved_tickets.get(ch, set()))
            cohort_stats[ch] = {
                "tickets": total_c,
                "resolved": resolved_c,
                "ruptured": ruptured_c,
            }
            risk = (ruptured_c / total_c) if total_c > 0 else 0.0
            cohort_rupture_risk[ch] = round(risk, 4)

        return {
            "total_tickets": self.total_tickets,
            "total_resolved": self.total_resolved,
            "total_failed": self.total_failed,
            "total_unknown": self.total_unknown,
            "total_complaints": self.total_complaints,
            "autonomous_resolved": self.autonomous_resolved_count,
            "overall_resolution_rate": round(overall_rate, 4),
            "autonomous_resolution_rate": round(auto_rate, 4),
            "hitl_rate": round(hitl_rate, 4),
            "cost_tier_distribution": dict(self.cost_tier_counts),
            "m_delta_transitions": self.m_delta_transitions,
            "promotions_count": self.promotions_count,
            "cohort_metrics": cohort_stats,
            "cohort_rupture_risk": cohort_rupture_risk,
        }
