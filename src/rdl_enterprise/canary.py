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


class ActionCapability(str, Enum):
    """外界作用の可逆性・補償可能性の分類"""
    REVERSIBLE = "reversible"         # 完全可逆 (未送信キュー削除、内部DBロールバック等)
    COMPENSATABLE = "compensatable"   # 補償可能 (訂正送信、返金API、取消チケット発行等)
    IRREVERSIBLE = "irreversible"     # 不可逆 (外部破棄API、即時実体変更等)
    DRY_RUN_ONLY = "dry_run_only"     # 副作用なし (読み取り専用、シミュレーション)


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
    deployment_id: Optional[str] = None            # 実行時のカナリア展開セッションID (スコープ境界)
    proposal_id: Optional[str] = None              # 紐づく再編プロポーザルID
    capability: ActionCapability = ActionCapability.COMPENSATABLE
    is_reversible: bool = True                     # 可逆（取り消し可能）か (後方互換プロパティ兼用)
    compensating_action: Optional[Dict[str, Any]] = None # 補償アクション (Undo定義)
    executed_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    status: str = "executed"                       # "executed" | "planned" | "attempted" | "succeeded" | "failed" | "uncompensated_irreversible"
    compensation_executed_at: Optional[str] = None
    compensation_result: Optional[Dict[str, Any]] = None

    @property
    def is_compensated(self) -> bool:
        """外界への作用が実際に取り消し・補償成功したか（単なる記録ではなく外界成功を要求）"""
        return self.status == "succeeded"


class CompensationExecutor:
    """外界作用の補償 (World Rollback) 実行インターフェース"""
    def __init__(self):
        self.handlers: Dict[str, Any] = {}

    def register_handler(self, action_key: str, handler: Any):
        """特定の action_type または compensation_type に対する補償ハンドラ関数を登録: fn(action_record) -> Dict[str, Any]"""
        self.handlers[action_key] = handler

    def execute_compensation(self, action_record: ActionRecord) -> Dict[str, Any]:
        """
        補償アクション（Undo API、訂正メッセージ送信等）を外界システムに対して実行する。
        登録ハンドラがあればそれを優先実行し、なければデフォルト実行。
        """
        if not action_record.compensating_action:
            return {"success": False, "reason": "補償アクション未定義"}

        comp_type = action_record.compensating_action.get("type")
        if comp_type and comp_type in self.handlers:
            try:
                return self.handlers[comp_type](action_record)
            except Exception as ex:
                return {"success": False, "error": str(ex)}

        if action_record.action_type in self.handlers:
            try:
                return self.handlers[action_record.action_type](action_record)
            except Exception as ex:
                return {"success": False, "error": str(ex)}

        # ハンドラが未登録の場合は fail-closed 原則（ゼロトラスト: 公理B5）に基づき失敗とする
        return {
            "success": False,
            "action_id": action_record.action_id,
            "reason": f"補償ハンドラ未登録 (fail-closed: comp_type={comp_type}, action_type={action_record.action_type})",
            "timestamp": datetime.utcnow().isoformat(),
        }


class ActionLedger:
    """外界作用監査台帳 (Model Rollback と World Rollback の架け橋)"""
    def __init__(self, default_executor: Optional[CompensationExecutor] = None):
        self.records: List[ActionRecord] = []
        self.executor = default_executor or CompensationExecutor()

    def record_action(
        self,
        ticket_id: str,
        mb_version: str,
        is_canary: bool,
        action_type: str,
        payload: Any,
        deployment_id: Optional[str] = None,
        proposal_id: Optional[str] = None,
        is_reversible: Optional[bool] = None,
        capability: Optional[ActionCapability] = None,
        compensating_action: Optional[Dict[str, Any]] = None,
    ) -> ActionRecord:
        action_id = f"act_{len(self.records) + 1:04d}"

        # capability 自動推定と is_reversible 整合
        if capability is None:
            if is_reversible is False:
                cap = ActionCapability.IRREVERSIBLE
            elif action_type in ("dry_run", "read_only"):
                cap = ActionCapability.DRY_RUN_ONLY
            elif action_type in ("payment", "delete_permanent", "external_irreversible"):
                cap = ActionCapability.IRREVERSIBLE
            elif compensating_action is not None or action_type in ("direct_reply", "email", "ticket_update"):
                cap = ActionCapability.COMPENSATABLE
            elif is_reversible is True or is_reversible is None:
                cap = ActionCapability.REVERSIBLE
            else:
                cap = ActionCapability.COMPENSATABLE
        else:
            cap = capability

        rev = (cap in (ActionCapability.REVERSIBLE, ActionCapability.COMPENSATABLE, ActionCapability.DRY_RUN_ONLY))
        if is_reversible is not None:
            rev = is_reversible

        rec = ActionRecord(
            action_id=action_id,
            ticket_id=ticket_id,
            mb_version=mb_version,
            is_canary=is_canary,
            action_type=action_type,
            payload=payload,
            deployment_id=deployment_id,
            proposal_id=proposal_id,
            capability=cap,
            is_reversible=rev,
            compensating_action=compensating_action,
        )
        self.records.append(rec)
        return rec

    def compensate_canary_actions(
        self,
        deployment_id: str,
        executor: Optional[CompensationExecutor] = None,
    ) -> List[Dict[str, Any]]:
        """
        ロールバック対象の deployment_id に限定して補償アクションを実行
        (他セッションのカナリア作用や本番作用を巻き込まないスコープ境界)
        """
        active_executor = executor or self.executor
        compensated = []

        for rec in reversed(self.records):
            if rec.deployment_id == deployment_id and rec.is_canary and rec.status in ("executed", "planned"):
                if rec.capability == ActionCapability.DRY_RUN_ONLY:
                    rec.status = "succeeded"
                    rec.compensation_executed_at = datetime.utcnow().isoformat()
                    compensated.append({
                        "action_id": rec.action_id,
                        "ticket_id": rec.ticket_id,
                        "deployment_id": rec.deployment_id,
                        "action_type": rec.action_type,
                        "capability": rec.capability.value,
                        "status": rec.status,
                        "note": "dry_run_only のため補償不要で成功",
                    })
                elif rec.capability == ActionCapability.IRREVERSIBLE or not rec.is_reversible:
                    rec.status = "uncompensated_irreversible"
                    compensated.append({
                        "action_id": rec.action_id,
                        "ticket_id": rec.ticket_id,
                        "deployment_id": rec.deployment_id,
                        "action_type": rec.action_type,
                        "capability": rec.capability.value,
                        "status": rec.status,
                        "warning": "不可逆な作用のため補償できませんでした",
                    })
                elif rec.compensating_action:
                    rec.status = "attempted"
                    exec_res = active_executor.execute_compensation(rec)
                    rec.status = "succeeded" if exec_res.get("success") else "failed"
                    rec.compensation_executed_at = datetime.utcnow().isoformat()
                    rec.compensation_result = exec_res
                    compensated.append({
                        "action_id": rec.action_id,
                        "ticket_id": rec.ticket_id,
                        "deployment_id": rec.deployment_id,
                        "action_type": rec.action_type,
                        "capability": rec.capability.value,
                        "compensating_action": rec.compensating_action,
                        "status": rec.status,
                        "executor_result": exec_res,
                    })
                else:
                    rec.status = "failed"
                    compensated.append({
                        "action_id": rec.action_id,
                        "ticket_id": rec.ticket_id,
                        "deployment_id": rec.deployment_id,
                        "action_type": rec.action_type,
                        "capability": rec.capability.value,
                        "status": rec.status,
                        "warning": "補償アクションが未定義です",
                    })
        return compensated


@dataclass
class CanaryDeployment:
    """カナリア展開セッション情報"""
    deployment_id: str
    proposal_id: str
    prod_mb_backup: MBGraph                     # ロールバック用の旧本番スナップショット
    canary_mb: MBGraph                          # 新昇格候補グラフ (Freeze済み)
    target_domain: str                          # 対象業務ドメイン
    traffic_ratio: float = 0.1                  # カナリア配分率 [0.0, 1.0] (初期10%)
    status: CanaryStatus = CanaryStatus.ACTIVE
    theta_canary: float = 1.5                   # カナリア許容発熱閾値
    max_allowed_failures: int = 1               # 許容失敗・差し戻し件数
    candidate_version: str = "unknown"          # 候補バージョン
    candidate_content_hash: str = "unknown"     # 候補の決定論的コンテンツハッシュ
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
        """新規カナリア展開を開始 (旧本番のバックアップを隔離保存し、候補を完全Freeze)"""
        # 旧本番のディープコピー保存
        backup_dict = current_prod_mb.to_dict()
        prod_backup = MBGraph.from_dict(backup_dict)

        # 候補グラフを完全 Freeze（Canary 期間中の局所学習・Identity Drift を防止）
        if hasattr(candidate_mb, "freeze"):
            candidate_mb.freeze()

        cand_ver = getattr(candidate_mb, "version", "unknown")
        cand_hash = candidate_mb.content_hash() if hasattr(candidate_mb, "content_hash") else "unknown"
        dep_id = f"dep_{proposal_id}_{len(self.deployment_history) + 1:03d}"

        deployment = CanaryDeployment(
            deployment_id=dep_id,
            proposal_id=proposal_id,
            prod_mb_backup=prod_backup,
            canary_mb=candidate_mb,
            target_domain=target_domain,
            traffic_ratio=initial_ratio,
            theta_canary=theta_canary,
            max_allowed_failures=max_allowed_failures,
            candidate_version=cand_ver,
            candidate_content_hash=cand_hash,
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
        current_heat: Optional[float] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        フィードバック受領時のカナリア発熱監視と自動ロールバック判定
        戻り値: (is_rolled_back, rollback_reason)
        """
        if not self.active_deployment or not is_canary or self.active_deployment.status != CanaryStatus.ACTIVE:
            return False, None

        dep = self.active_deployment
        dep.canary_cases_count += 1
        case_heat = e_pred + 0.4 * e_input

        # HState の統一熱が渡されている場合はそれを信頼できる累積熱として同期
        if current_heat is not None:
            dep.canary_heat = current_heat
        else:
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
        """
        全面展開完了: カナリア新 M_B' を本番として確定。
        ポリシー未指定時でもデフォルトの CanaryCompletionPolicy を強制適用し、
        さらに検査時の candidate_content_hash との完全一致を検証する (公理B5)。
        """
        if not self.active_deployment or self.active_deployment.status != CanaryStatus.ACTIVE:
            return None

        effective_policy = policy or CanaryCompletionPolicy()
        can_complete, reasons = self.evaluate_completion_readiness(effective_policy)
        if not can_complete:
            return None

        dep = self.active_deployment

        # ハッシュ一致検証 (Freeze状態が維持され、1ビットも変質していないこと)
        if hasattr(dep.canary_mb, "content_hash"):
            current_hash = dep.canary_mb.content_hash()
            if dep.candidate_content_hash != "unknown" and current_hash != dep.candidate_content_hash:
                # 候補の変質（Identity Drift）を検知したためロールバック
                self.trigger_rollback(f"コミット時ハッシュ不一致検知 (Identity Drift: {current_hash} != {dep.candidate_content_hash})")
                return None

        dep.status = CanaryStatus.COMPLETED
        dep.traffic_ratio = 1.0
        dep.completed_at = datetime.utcnow().isoformat()
        if hasattr(dep.canary_mb, "unfreeze"):
            dep.canary_mb.unfreeze()  # 本番運用移行のため凍結解除
        self.deployment_history.append(dep)
        self.active_deployment = None
        return dep.canary_mb

    def trigger_rollback(self, reason: str, executor: Optional[CompensationExecutor] = None) -> Optional[MBGraph]:
        """自動または手動ロールバック: 旧本番 M_B を復元し、該当 deployment_id の外界補償アクションを実行"""
        if not self.active_deployment or self.active_deployment.status != CanaryStatus.ACTIVE:
            return None

        dep = self.active_deployment
        # 該当セッション(deployment_id)にスコープを限定して補償実行
        comp_logs = self.action_ledger.compensate_canary_actions(dep.deployment_id, executor=executor)
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


class InMemoryCompensationClient:
    """
    テスト・シミュレーション用の外部補償APIクライアント (実外界接続の安全な検証器)
    送信された訂正通知や取り消しリクエストをメモリ上に監査記録する。
    """
    def __init__(self, should_succeed: bool = True):
        self.sent_reverts: List[Dict[str, Any]] = []
        self.should_succeed = should_succeed

    def send_revert(self, action_record: ActionRecord) -> Dict[str, Any]:
        if not self.should_succeed:
            return {"success": False, "reason": "外部補償APIモックエラー"}

        entry = {
            "action_id": action_record.action_id,
            "ticket_id": action_record.ticket_id,
            "compensating_action": action_record.compensating_action,
            "timestamp": datetime.utcnow().isoformat(),
        }
        self.sent_reverts.append(entry)
        return {
            "success": True,
            "external_receipt_id": f"rec_{len(self.sent_reverts):04d}",
            "sent_entry": entry,
        }

