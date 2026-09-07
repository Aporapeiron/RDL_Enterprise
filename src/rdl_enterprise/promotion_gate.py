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
class EvidenceRequirement:
    """Shadow評価における証拠の質・境界カバレッジ要件"""
    minimum_cases: int = 1                         # 最小解決事例数
    minimum_unique_patterns: int = 1               # 最小ユニーククエリ数 (多様性・偏り排除)
    required_categories: List[str] = field(default_factory=list) # 必須カバー業務カテゴリ (境界Bの網羅)
    require_improved_case: bool = False           # 少なくとも1件以上の改善実績 (E_shadow < E_prod) を要求するか


@dataclass
class PromotionPolicy:
    """リスク階層に応じた昇格検証ポリシー"""
    risk_level: str = "medium"                            # "low" | "medium" | "high"
    require_durability: bool = True                       # DurabilityHarness 必須か
    require_shadow: bool = True                           # Shadow Execution 必須か
    minimum_shadow_cases: int = 1                         # (後方互換用エイリアス)
    max_allowed_regression_rate: float = 0.05             # 許容改悪率
    require_human_approval: bool = True                   # 人間承認を必須とするか (Falseなら委任権限で自動昇格可)
    evidence_requirement: Optional[EvidenceRequirement] = None # 証拠カバレッジ要件

    def get_evidence_requirement(self) -> EvidenceRequirement:
        """証拠要件を取得 (未設定時は minimum_shadow_cases から自動構成)"""
        if self.evidence_requirement is not None:
            return self.evidence_requirement
        min_cases = self.minimum_shadow_cases
        return EvidenceRequirement(
            minimum_cases=min_cases,
            minimum_unique_patterns=max(1, min_cases) if min_cases > 0 else 0,
        )

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
                evidence_requirement=EvidenceRequirement(
                    minimum_cases=2,
                    minimum_unique_patterns=2,
                    require_improved_case=True,
                ),
            )
        elif domain in ("workflow", "operations"):
            return cls(
                risk_level="medium",
                require_durability=True,
                require_shadow=True,
                minimum_shadow_cases=1,
                max_allowed_regression_rate=0.05,
                require_human_approval=True,
                evidence_requirement=EvidenceRequirement(
                    minimum_cases=1,
                    minimum_unique_patterns=1,
                    require_improved_case=False,
                ),
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
                evidence_requirement=EvidenceRequirement(
                    minimum_cases=0,
                    minimum_unique_patterns=0,
                    require_improved_case=False,
                ),
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
        candidate_mb: Optional[Any] = None,
    ) -> GateEvaluationResult:
        reasons = []

        # 0. 候補実体とのハッシュバインディング検証 (公理B5: Identity Drift の防止)
        if candidate_mb and hasattr(candidate_mb, "content_hash"):
            current_hash = candidate_mb.content_hash()
            if durability_result and "candidate_content_hash" in durability_result:
                dur_hash = durability_result.get("candidate_content_hash")
                if dur_hash and dur_hash != current_hash:
                    return GateEvaluationResult(
                        can_promote=False,
                        current_state=current_state,
                        next_state=ProposalState.REJECTED,
                        reasons=[f"Durability検査時の候補ハッシュと現在の候補ハッシュが一致しません (Identity Drift検知: {dur_hash} != {current_hash})"],
                    )
            if shadow_report and shadow_report.candidate_content_hash:
                shad_hash = shadow_report.candidate_content_hash
                if shad_hash != current_hash:
                    return GateEvaluationResult(
                        can_promote=False,
                        current_state=current_state,
                        next_state=ProposalState.REJECTED,
                        reasons=[f"Shadow検査時の候補ハッシュと現在の候補ハッシュが一致しません (Identity Drift検知: {shad_hash} != {current_hash})"],
                    )

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

        # 3. Shadow 検証 (Evidence Coverage & Diversity)
        if policy.require_shadow:
            if not shadow_report:
                return GateEvaluationResult(
                    can_promote=False,
                    current_state=new_state,
                    next_state=ProposalState.SHADOW_RUNNING,
                    reasons=["ポリシー上 Shadow 並行推論が必須ですが、レポートがありません"],
                )

            ev_req = policy.get_evidence_requirement()

            # (a) 最小件数チェック
            if (
                shadow_report.evaluation_status == "insufficient_evidence"
                or shadow_report.resolved_triplets_count < ev_req.minimum_cases
            ):
                return GateEvaluationResult(
                    can_promote=False,
                    current_state=ProposalState.SHADOW_RUNNING,
                    next_state=ProposalState.INSUFFICIENT_EVIDENCE,
                    reasons=[f"Shadow 解決事例が不足しています ({shadow_report.resolved_triplets_count}/{ev_req.minimum_cases}件)"],
                )

            # (b) 破断面多様性チェック (境界・ノード・故障モードの多次元網羅: 偏った同一パターンの排除)
            patterns_count = shadow_report.unique_patterns_count or shadow_report.unique_queries_count
            if patterns_count < ev_req.minimum_unique_patterns:
                return GateEvaluationResult(
                    can_promote=False,
                    current_state=ProposalState.SHADOW_RUNNING,
                    next_state=ProposalState.INSUFFICIENT_EVIDENCE,
                    reasons=[f"Shadow 破断面多様性が不足しています ({patterns_count}/{ev_req.minimum_unique_patterns}パターン: 異なる破断面・境界を十分に通過していません)"],
                )

            # (c) 必須カテゴリ網羅チェック (有限境界 B のカバー)
            if ev_req.required_categories:
                missing = [c for c in ev_req.required_categories if c not in shadow_report.covered_categories]
                if missing:
                    return GateEvaluationResult(
                        can_promote=False,
                        current_state=ProposalState.SHADOW_RUNNING,
                        next_state=ProposalState.INSUFFICIENT_EVIDENCE,
                        reasons=[f"Shadow で必須カテゴリが未カバーです (未網羅: {missing})"],
                    )

            # (d) 改善実績の要求チェック
            if ev_req.require_improved_case and shadow_report.improved_count == 0:
                return GateEvaluationResult(
                    can_promote=False,
                    current_state=ProposalState.SHADOW_RUNNING,
                    next_state=ProposalState.INSUFFICIENT_EVIDENCE,
                    reasons=["ポリシー上改善実績 (E_shadow < E_prod) が最低1件必須ですが、改善事例が確認できませんでした"],
                )

            # (e) 改悪率チェック
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
            reasons=["全ポリシー基準 (Durability / Shadow Evidence) を満たし、承認準備が完了しました"],
        )

    @staticmethod
    def verify_authority_for_promotion(
        authority: AuthorityContext,
        target_domain: str,
        policy: PromotionPolicy,
        is_automated: bool = False,
    ) -> bool:
        """権限者のスコープおよび人間承認の真正性 (Human Identity Proof) の検証"""
        # ドメイン権限チェック
        if not authority.is_authorized_for(target_domain):
            return False

        # 人間承認ポリシーの検証
        if policy.require_human_approval:
            # 1. 呼び出し元が自動化フラグを立てている場合は拒絶
            if is_automated:
                return False
            # 2. 真正な人間認証 (Human Identity Proof) を検証
            # (actor_type == 'human' かつ 信頼できる対人認証方式)
            if not authority.is_human_authenticated():
                return False

        return True