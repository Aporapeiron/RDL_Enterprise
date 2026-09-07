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
class CanaryCompletionPolicy:
    """カナリア全面展開完了のためのエビデンス検証ポリシー"""
    minimum_cases: int = 1                         # カナリアで処理すべき最小解決件数
    minimum_successes: int = 1                     # カナリアで確認すべき最小成功件数
    max_allowed_failure_rate: float = 0.05         # 許容最大失敗率 (5%)
    max_allowed_heat: float = 1.0                  # 許容累積熱 H_canary


@dataclass
class ActionRecord:
    """AIが外界へ及ぼした作用・ツールの監査ログ"""
    action_id: str
    ticket_id: str
    mb_version: str
    is_canary: bool
    action_type: str                               # "direct_reply" | "tool_call" | "email" | "db_write" | "external_api"
    payload: Any
    is_reversible: bool = True                     # 可逆（取り消し可能）か
    compensating_action: Optional[Dict[str, Any]] = None # 補償アクション (Undo定義)
    executed_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    status: str = "executed"                       # "executed" | "compensated" | "dry_run" | "uncompensated_irreversible"
    compensation_executed_at: Optional[str] = None

    @property
    def is_compensated(self) -> bool:
        return self.status == "compensated"


class ActionLedger:
    """外界作用監査台帳 (Model Rollback と World Rollback の架け橋)"""
    def __init__(self):
        self.records: List[ActionRecord] = []

    def record_action(
        self,
        ticket_id: str,
        mb_version: str,
        is_canary: bool,
        action_type: str,
        payload: Any,
        is_reversible: bool = True,
        compensating_action: Optional[Dict[str, Any]] = None,
    ) -> ActionRecord:
        action_id = f"act_{len(self.records) + 1:04d}"
        rec = ActionRecord(
            action_id=action_id,
            ticket_id=ticket_id,
            mb_version=mb_version,
            is_canary=is_canary,
            action_type=action_type,
            payload=payload,
            is_reversible=is_reversible,
            compensating_action=compensating_action,
        )
        self.records.append(rec)
        return rec

    def compensate_canary_actions(self, proposal_id: str) -> List[Dict[str, Any]]:
        """カナリア期間中に実行された作用に対して補償アクションを実行"""
        compensated = []
        for rec in reversed(self.records):
            if rec.is_canary and rec.status == "executed":
                if rec.compensating_action:
                    rec.status = "compensated"
                    rec.compensation_executed_at = datetime.utcnow().isoformat()
                    compensated.append({
                        "action_id": rec.action_id,
                        "ticket_id": rec.ticket_id,
                        "action_type": rec.action_type,
                        "compensating_action": rec.compensating_action,
                        "status": "compensated",
                    })
                else:
                    rec.status = "uncompensated_irreversible" if not rec.is_reversible else "acknowledged"
                    compensated.append({
                        "action_id": rec.action_id,
                        "ticket_id": rec.ticket_id,
                        "action_type": rec.action_type,
                        "status": rec.status,
                        "warning": "補償アクションが未定義です",
                    })
        return compensated


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
        self.action_ledger = ActionLedger()

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

    def evaluate_completion_readiness(self, policy: CanaryCompletionPolicy) -> Tuple[bool, List[str]]:
        """完了ポリシーに対するエビデンス検証"""
        if not self.active_deployment or self.active_deployment.status != CanaryStatus.ACTIVE:
            return False, ["アクティブなカナリア展開が存在しません"]

        dep = self.active_deployment
        reasons = []
        if dep.canary_cases_count < policy.minimum_cases:
            reasons.append(f"カナリア解決事例が不足しています ({dep.canary_cases_count}/{policy.minimum_cases}件)")
        if dep.canary_success_count < policy.minimum_successes:
            reasons.append(f"カナリア成功事例が不足しています ({dep.canary_success_count}/{policy.minimum_successes}件)")
        if dep.canary_cases_count > 0:
            failure_rate = dep.canary_failure_count / dep.canary_cases_count
            if failure_rate > policy.max_allowed_failure_rate:
                reasons.append(f"カナリア失敗率が許容値を超過しています ({failure_rate*100:.1f}% > {policy.max_allowed_failure_rate*100:.1f}%)")
        if dep.canary_heat > policy.max_allowed_heat:
            reasons.append(f"カナリア累積熱が許容値を超過しています (H_canary={dep.canary_heat:.2f} > {policy.max_allowed_heat:.2f})")

        return (len(reasons) == 0), reasons

    def complete_rollout(self, policy: Optional[CanaryCompletionPolicy] = None) -> Optional[MBGraph]:
        """全面展開完了: カナリア新 M_B' を本番として確定 (ポリシー指定時は検証実行)"""
        if not self.active_deployment or self.active_deployment.status != CanaryStatus.ACTIVE:
            return None

        if policy is not None:
            can_complete, reasons = self.evaluate_completion_readiness(policy)
            if not can_complete:
                return None

        dep = self.active_deployment
        dep.status = CanaryStatus.COMPLETED
        dep.traffic_ratio = 1.0
        dep.completed_at = datetime.utcnow().isoformat()
        self.deployment_history.append(dep)
        self.active_deployment = None
        return dep.canary_mb

    def trigger_rollback(self, reason: str) -> Optional[MBGraph]:
        """自動または手動ロールバック: 旧本番 M_B を復元し、外界補償アクションを実行"""
        if not self.active_deployment or self.active_deployment.status != CanaryStatus.ACTIVE:
            return None

        dep = self.active_deployment
        comp_logs = self.action_ledger.compensate_canary_actions(dep.proposal_id)
        dep.status = CanaryStatus.ROLLED_BACK
        dep.rolled_back_at = datetime.utcnow().isoformat()
        dep.rollback_reason = reason
        dep.audit_log.append({
            "action": "rollback",
            "reason": reason,
            "compensated_actions_count": len(comp_logs),
            "compensation_details": comp_logs,
            "timestamp": datetime.utcnow().isoformat(),
        })
        self.deployment_history.append(dep)
        self.active_deployment = None
        return dep.prod_mb_backup
