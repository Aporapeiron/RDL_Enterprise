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
    tickets_today: int = 0
    resolved_today: int = 0
    failed_today: int = 0
    unknown_today: int = 0
    cost_tier_today: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    heat_level: float = 0.0
    theta_eff: float = 0.0
    average_kappa: float = 0.0
    total_inertia: float = 0.0


class SimMetricsCollector:
    """
    シミュレーション全体の統計情報およびコホート別メトリクスを収集・集計する。
    """
    def __init__(self):
        self.total_tickets: int = 0
        self.total_resolved: int = 0
        self.total_failed: int = 0
        self.total_unknown: int = 0
        self.total_complaints: int = 0
        self.hitl_count: int = 0
        self.m_delta_transitions: int = 0
        self.promotions_count: int = 0

        self.cost_tier_counts: Dict[int, int] = defaultdict(int)
        
        # コホート別集計 (初心者, ベテラン, 短気, セキュリティ等)
        self.cohort_metrics: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"tickets": 0, "resolved": 0, "failed": 0, "unknown": 0, "complaints": 0}
        )

        # 日次推移
        self.daily_snapshots: List[DailySnapshot] = []

    def record_ticket(
        self,
        cost_tier: int,
        cohort: str = "general",
        hitl_triggered: bool = False,
    ) -> None:
        self.total_tickets += 1
        self.cost_tier_counts[cost_tier] += 1
        self.cohort_metrics[cohort]["tickets"] += 1
        if hitl_triggered:
            self.hitl_count += 1

    def record_feedback(
        self,
        cohort: str,
        user_resolved: Optional[bool],
        complaint: bool = False,
    ) -> None:
        if user_resolved is True:
            self.total_resolved += 1
            self.cohort_metrics[cohort]["resolved"] += 1
        elif user_resolved is False:
            self.total_failed += 1
            self.cohort_metrics[cohort]["failed"] += 1
        else:
            self.total_unknown += 1
            self.cohort_metrics[cohort]["unknown"] += 1

        if complaint:
            self.total_complaints += 1
            self.cohort_metrics[cohort]["complaints"] += 1

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
        snap = DailySnapshot(
            day=day,
            tick=tick,
            tickets_today=self.total_tickets,
            resolved_today=self.total_resolved,
            failed_today=self.total_failed,
            unknown_today=self.total_unknown,
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
        auto_rate = (self.total_resolved / total_finished) if total_finished > 0 else 0.0
        hitl_rate = (self.hitl_count / self.total_tickets) if self.total_tickets > 0 else 0.0

        # コホート別破断率 (失敗 + 苦情 / 案件数)
        cohort_rupture_risk: Dict[str, float] = {}
        for ch, data in self.cohort_metrics.items():
            cnt = data["tickets"]
            if cnt > 0:
                rupture_risk = (data["failed"] + data["complaints"] + data["unknown"]) / cnt
                cohort_rupture_risk[ch] = round(rupture_risk, 3)

        return {
            "total_tickets": self.total_tickets,
            "total_resolved": self.total_resolved,
            "total_failed": self.total_failed,
            "total_unknown": self.total_unknown,
            "total_complaints": self.total_complaints,
            "auto_resolution_rate": round(auto_rate, 4),
            "hitl_rate": round(hitl_rate, 4),
            "cost_tier_distribution": dict(self.cost_tier_counts),
            "m_delta_transitions": self.m_delta_transitions,
            "promotions_count": self.promotions_count,
            "cohort_metrics": dict(self.cohort_metrics),
            "cohort_rupture_risk": cohort_rupture_risk,
        }
