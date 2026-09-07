"""
RDL Enterprise: Canary Deployment & Automated Rollback
本番置換 (Leap) 後の段階的トラフィック配分と、実稼働ノードの発熱監視・自動ロールバック。

RDL力学原則:
- 昇格後であっても、外界との接触によって予期せぬ不整合 (E) と熱 (H_canary) が蓄積しうる。
- カナリア許容熱 θ_canary を超えた場合、生存優先（Survival-bias）に基づき即座に旧 M_B へ退避 (Rollback) する。
"""

import hashlib
from enum import Enum
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime

from .mb_graph import MBGraph
from .snapshot import BusinessInput


class CanaryStatus(str, Enum):
    """カナリア展開の状態"""
    ACTIVE = "active"             # 段階的配分・監視実行中
    COMPLETED = "completed"       # 全面展開完了 (100% コミット)
    ROLLED_BACK = "rolled_back"   # 異常発熱・差し戻しにより旧本番へロールバック


@dataclass
class CanaryDeployment:
    """カナリア展開セッション情報"""
    proposal_id: str
    prod_mb_backup: MBGraph                     # ロールバック用の旧本番スナップショット
    canary_mb: MBGraph                          # 新昇格候補グラフ
    target_domain: str                          # 対象業務ドメイン
    traffic_ratio: float = 0.1                  # カナリア配分率 [0.0, 1.0] (初期10%)
    status: CanaryStatus = CanaryStatus.ACTIVE
    theta_canary: float = 1.5                   # カナリア許容発熱閾値
    max_allowed_failures: int = 1               # 許容失敗・差し戻し件数
    canary_cases_count: int = 0                 # カナリア処理総数
    canary_success_count: int = 0               # カナリア成功数
    canary_failure_count: int = 0               # カナリア失敗数
    canary_heat: float = 0.0                    # カナリア累積熱 H_canary
    started_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    completed_at: Optional[str] = None
    rolled_back_at: Optional[str] = None
    rollback_reason: Optional[str] = None
    audit_log: List[Dict[str, Any]] = field(default_factory=list)


class CanaryManager:
    """
    カナリア展開・監視マネージャー:
    チケットを段階的にカナリア新 M_B' へ振り分け、実結果による発熱 (H_canary) を監視。
    閾値破断時は自動的に旧 M_B へロールバックする。
    """
    def __init__(self):
        self.active_deployment: Optional[CanaryDeployment] = None
        self.deployment_history: List[CanaryDeployment] = []

    def start_canary(
        self,
        proposal_id: str,
        current_prod_mb: MBGraph,
        candidate_mb: MBGraph,
        target_domain: str,
        initial_ratio: float = 0.1,
        theta_canary: float = 1.5,
        max_allowed_failures: int = 1,
    ) -> CanaryDeployment:
        """新規カナリア展開を開始 (旧本番のバックアップを隔離保存)"""
        # 旧本番のディープコピー保存
        backup_dict = current_prod_mb.to_dict()
        prod_backup = MBGraph.from_dict(backup_dict)

        deployment = CanaryDeployment(
            proposal_id=proposal_id,
            prod_mb_backup=prod_backup,
            canary_mb=candidate_mb,
            target_domain=target_domain,
            traffic_ratio=initial_ratio,
            theta_canary=theta_canary,
            max_allowed_failures=max_allowed_failures,
        )
        self.active_deployment = deployment
        return deployment

    def should_route_to_canary(self, efp: BusinessInput) -> bool:
        """
        チケットがカナリア対象か判定:
        1. カナリア展開中であること
        2. チケットのドメインが対象ドメイン (または 'all') に合致すること
        3. 決定論的ハッシュによる traffic_ratio 判定
        """
        if not self.active_deployment or self.active_deployment.status != CanaryStatus.ACTIVE:
            return False

        dep = self.active_deployment
        # ドメイン境界チェック
        if dep.target_domain not in ("all", "*", None) and efp.category != dep.target_domain:
            return False

        if dep.traffic_ratio >= 1.0:
            return True
        if dep.traffic_ratio <= 0.0:
            return False

        # チケットIDを用いた決定論的ハッシュ [0.0, 1.0)
        h = int(hashlib.md5(efp.ticket_id.encode("utf-8")).hexdigest()[:8], 16)
        normalized_ratio = (h % 10000) / 10000.0
        return normalized_ratio < dep.traffic_ratio

    def record_feedback(
        self,
        ticket_id: str,
        is_canary: bool,
        e_pred: float,
        e_input: float,
        rejected: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """
        フィードバック受領時のカナリア発熱監視と自動ロールバック判定
        戻り値: (is_rolled_back, rollback_reason)
        """
        if not self.active_deployment or not is_canary or self.active_deployment.status != CanaryStatus.ACTIVE:
            return False, None

        dep = self.active_deployment
        dep.canary_cases_count += 1
        case_heat = e_pred + e_input
        dep.canary_heat += case_heat

        is_failure = rejected or (e_pred > 0.5)
        if is_failure:
            dep.canary_failure_count += 1
        else:
            dep.canary_success_count += 1

        dep.audit_log.append({
            "ticket_id": ticket_id,
            "case_heat": case_heat,
            "cumulative_heat": dep.canary_heat,
            "is_failure": is_failure,
            "rejected": rejected,
            "timestamp": datetime.utcnow().isoformat(),
        })

        # 自動ロールバック判定 (発熱破断 または 差し戻し件数超過)
        rollback_reason = None
        if dep.canary_heat >= dep.theta_canary:
            rollback_reason = f"カナリア累積熱超過: H_canary={dep.canary_heat:.2f} >= θ_canary={dep.theta_canary:.2f}"
        elif dep.canary_failure_count > dep.max_allowed_failures:
            rollback_reason = f"カナリア失敗許容数超過: failures={dep.canary_failure_count} > max={dep.max_allowed_failures}"

        if rollback_reason:
            self.trigger_rollback(rollback_reason)
            return True, rollback_reason

        return False, None

    def step_up_traffic(self, new_ratio: float) -> bool:
        """カナリア配分比率を拡大 (例: 0.1 -> 0.5 -> 1.0)"""
        if not self.active_deployment or self.active_deployment.status != CanaryStatus.ACTIVE:
            return False

        clamped = max(0.0, min(1.0, new_ratio))
        self.active_deployment.traffic_ratio = clamped
        self.active_deployment.audit_log.append({
            "action": "step_up",
            "new_ratio": clamped,
            "timestamp": datetime.utcnow().isoformat(),
        })
        return True

    def complete_rollout(self) -> Optional[MBGraph]:
        """全面展開完了: カナリア新 M_B' を本番として確定"""
        if not self.active_deployment or self.active_deployment.status != CanaryStatus.ACTIVE:
            return None

        dep = self.active_deployment
        dep.status = CanaryStatus.COMPLETED
        dep.traffic_ratio = 1.0
        dep.completed_at = datetime.utcnow().isoformat()
        self.deployment_history.append(dep)
        self.active_deployment = None
        return dep.canary_mb

    def trigger_rollback(self, reason: str) -> Optional[MBGraph]:
        """自動または手動ロールバック: 旧本番 M_B を復元"""
        if not self.active_deployment or self.active_deployment.status != CanaryStatus.ACTIVE:
            return None

        dep = self.active_deployment
        dep.status = CanaryStatus.ROLLED_BACK
        dep.rolled_back_at = datetime.utcnow().isoformat()
        dep.rollback_reason = reason
        self.deployment_history.append(dep)
        self.active_deployment = None
        return dep.prod_mb_backup
