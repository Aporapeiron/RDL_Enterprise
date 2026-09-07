"""
RDL Enterprise: Promotion Gate & Reorganization State Machine
再編候補 M_B' の昇格準備性 (Readiness) と承認権限 (Authority) を分離し、
客観的な検証基準とリスクベースポリシーに基づいて本番置換 (Leap) を統制する。
"""

from enum import Enum
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

from .authority import AuthorityContext
from .durability import DurabilityReport
from .shadow import ShadowReport


class ProposalState(str, Enum):
    """再編相 M_Δ プロポーザルの状態機械 (State Machine)"""
    DRAFT = "draft"                                       # 候補 M_B' 起草直後 (未検証)
    DURABILITY_PASSED = "durability_passed"               # 耐久ハーネス合格 (Replay / Boundary / Perturbation)
    SHADOW_RUNNING = "shadow_running"                     # シャドウ並行評価実行中
    SHADOW_PASSED = "shadow_passed"                       # シャドウ評価合格 (改悪率クリア・最小件数充足)
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"       # シャドウ検証件数不足
    REGRESSED = "regressed"                               # シャドウ評価で改悪が検出された (退行)
    APPROVAL_READY = "awaiting_approval"                 # ポリシー上の全検査をクリアし承認待ち
    AWAITING_APPROVAL = "awaiting_approval"               # 承認待ち (後方互換エイリアス)
    PROMOTED = "promoted"                                 # 権限者承認を経て本番置換 (Leap) 完了
    REJECTED = "rejected"                                 # 耐久破断または権限者により却下


@dataclass
class PromotionPolicy:
    """リスク階層に応じた昇格検証ポリシー"""
    risk_level: str = "medium"                            # "low" | "medium" | "high"
    require_durability: bool = True                       # DurabilityHarness 必須か
    require_shadow: bool = True                           # Shadow Execution 必須か
    minimum_shadow_cases: int = 1                         # シャドウに必要な最小解決案件数
    max_allowed_regression_rate: float = 0.05             # 許容改悪率
    require_human_approval: bool = True                   # 人間承認を必須とするか (Falseなら委任権限で自動昇格可)

    @classmethod
    def default_for_domain(cls, domain: str) -> "PromotionPolicy":
        """ドメイン境界に応じた標準ポリシーの決定"""
        if domain in ("security", "system", "auth"):
            # 特権・セキュリティ系は最厳格
            return cls(
                risk_level="high",
                require_durability=True,
                require_shadow=True,
                minimum_shadow_cases=2,
                max_allowed_regression_rate=0.0,
                require_human_approval=True,
            )
        elif domain in ("workflow", "operations"):
            return cls(
                risk_level="medium",
                require_durability=True,
                require_shadow=True,
                minimum_shadow_cases=1,
                max_allowed_regression_rate=0.05,
                require_human_approval=True,
            )
        else:
            # 一般問い合わせ・FAQ系
            return cls(
                risk_level="low",
                require_durability=True,
                require_shadow=False,
                minimum_shadow_cases=0,
                max_allowed_regression_rate=0.05,
                require_human_approval=False,
            )


@dataclass
class GateEvaluationResult:
    """昇格ゲートの評価結果"""
    can_promote: bool
    current_state: ProposalState
    next_state: ProposalState
    reasons: List[str] = field(default_factory=list)


class PromotionGate:
    """
    昇格ゲート検証器:
    プロポーザルの現在の状態、耐久結果、シャドウレポート、およびポリシーを突き合わせ、
    「昇格可能な状態 (APPROVAL_READY / PROMOTED) に達しているか」を判定する
    """

    @staticmethod
    def evaluate_readiness(
        current_state: ProposalState,
        durability_result: Optional[Dict[str, Any]],
        shadow_report: Optional[ShadowReport],
        policy: PromotionPolicy,
    ) -> GateEvaluationResult:
        reasons = []

        # 1. すでに終端状態の場合
        if current_state in (ProposalState.PROMOTED, ProposalState.REJECTED):
            return GateEvaluationResult(
                can_promote=False,
                current_state=current_state,
                next_state=current_state,
                reasons=[f"プロポーザルはすでに終端状態 ({current_state.value}) です"],
            )

        # 2. Durability 検証
        if policy.require_durability:
            if not durability_result or not durability_result.get("all_passed"):
                return GateEvaluationResult(
                    can_promote=False,
                    current_state=current_state,
                    next_state=ProposalState.REJECTED,
                    reasons=["耐久検査 (DurabilityHarness) に不合格です"],
                )

        # 耐久検査合格のマーク
        new_state = ProposalState.DURABILITY_PASSED

        # 3. Shadow 検証
        if policy.require_shadow:
            if not shadow_report:
                return GateEvaluationResult(
                    can_promote=False,
                    current_state=new_state,
                    next_state=ProposalState.SHADOW_RUNNING,
                    reasons=["ポリシー上 Shadow 並行推論が必須ですが、レポートがありません"],
                )

            if shadow_report.evaluation_status == "insufficient_evidence":
                return GateEvaluationResult(
                    can_promote=False,
                    current_state=ProposalState.SHADOW_RUNNING,
                    next_state=ProposalState.INSUFFICIENT_EVIDENCE,
                    reasons=[f"Shadow 解決事例が不足しています ({shadow_report.resolved_triplets_count}/{policy.minimum_shadow_cases}件)"],
                )

            if shadow_report.evaluation_status == "failed" or shadow_report.regression_rate > policy.max_allowed_regression_rate:
                return GateEvaluationResult(
                    can_promote=False,
                    current_state=ProposalState.SHADOW_RUNNING,
                    next_state=ProposalState.REGRESSED,
                    reasons=[f"Shadow 評価で改悪率が許容値を超過過大です ({shadow_report.regression_rate*100:.1f}%)"],
                )

            new_state = ProposalState.SHADOW_PASSED

        # 4. 承認準備完了
        return GateEvaluationResult(
            can_promote=True,
            current_state=new_state,
            next_state=ProposalState.APPROVAL_READY,
            reasons=["全ポリシー基準 (Durability / Shadow) を満たし、承認準備が完了しました"],
        )

    @staticmethod
    def verify_authority_for_promotion(
        authority: AuthorityContext,
        target_domain: str,
        policy: PromotionPolicy,
        is_automated: bool = False,
    ) -> bool:
        """権限者のスコープおよび自動昇格制限の検証"""
        # ドメイン権限チェック
        if not authority.is_authorized_for(target_domain):
            return False

        # 自動昇格の場合、ポリシーで人間承認必須が指定されていたら自動昇格は拒絶
        if is_automated and policy.require_human_approval:
            return False

        return True