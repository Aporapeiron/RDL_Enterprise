from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Tuple
from enum import Enum
from datetime import datetime

class CaseStatus(str, Enum):
    PENDING = "pending"     # 回答・アクション実行済み、結果（EFP'）待ち
    SUCCESS = "success"     # 解決 / 承認完了
    FAILURE = "failure"     # 未解決 / 苦情
    REJECTED = "rejected"   # 先輩/管理者による差し戻し
    UNKNOWN = "unknown"     # タイムアウト / 追跡不能


@dataclass
class BusinessInput:
    ticket_id: str
    user_id: str
    category: Optional[str]
    query_text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class InterpretationPrediction:
    """事前予測 F"""
    action_type: str                  # "direct_reply" | "tool_call" | "ask_human" | "delegate"
    content: str                      # 回答テキストまたは処理内容
    confidence: float                 # 確信度 [0.0, 1.0]
    matched_node_id: Optional[str]
    cost_tier: int                    # 0: ローカル最小, 1: ルール, 2: 局所推論, 3: 外部LLM
    domain: Optional[str] = None
    expected_outcome: str = "resolve" # "resolve" | "need_input" | "escalate"


@dataclass
class FeedbackResult:
    """事後結果 EFP'"""
    user_resolved: bool               # ユーザーが解決したか
    human_approved: bool = False      # 先輩/管理者が承認したか
    human_rejected: bool = False      # 先輩/管理者が差し戻したか
    actual_response_text: Optional[str] = None
    feedback_comment: Optional[str] = None
    new_knowledge_provided: Optional[str] = None


@dataclass
class FrozenInterpretationContext:
    """
    更新前の同一 M_B および推論環境の完全凍結スナップショット (T0 SPEC 4, 6.1)
    F (事前予測) と F' (事後解釈) を厳密に同一の前提のもとで形成するための暗号論的保証構造。
    """
    mb_version: str
    mb_content_hash: str
    frozen_mb: Any                              # MBGraph (Deep Freeze済み)
    target_domain: Optional[str] = None
    llm_bridge: Optional[Any] = None

    def interpret_efp(self, efp: BusinessInput) -> InterpretationPrediction:
        """更新前の同一 M_B 前提を用いて入力を解釈 (interp(M_B, EFP))"""
        if hasattr(self.frozen_mb, "content_hash"):
            current_h = self.frozen_mb.content_hash()
            if self.mb_content_hash != "unknown" and current_h != self.mb_content_hash:
                raise RuntimeError(f"FrozenInterpretationContext の変質を検知 (Identity Drift: {current_h} != {self.mb_content_hash})")

        from .cascade import InterpCascade
        cascade = InterpCascade(self.frozen_mb, llm_bridge=self.llm_bridge)
        return cascade.interpret(efp)


@dataclass
class SubsequentInterpretation:
    """
    後続作用解釈 F' (T0 SPEC 4, 6.1)
    後続する EFP' を、更新前の同一の M_B が解釈して形成した作用情報。
    """
    actual_status: CaseStatus                  # 解釈された帰結状態 (SUCCESS, FAILURE, REJECTED, UNKNOWN)
    actual_outcome: str                        # "resolved" | "unresolved" | "rejected"
    confidence_prime: float                    # 事後入力受領後の更新前構造における確信度評価
    matched_node_id: Optional[str]             # 更新前前提での該当ノードID
    e_prediction_delta: float                  # F と F' の間の予測不整合 Δ_pred(F, F')
    e_input_delta: float                       # 入力境界の不整合 Δ_input(EFP, EFP')
    explanation: str = ""                      # 解釈論理の記録
    interpreted_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class CaseSnapshot:
    """
    同一更新前 M_B に基づく F と F' の差分 E 算出器 (T0 SPEC 4, 6.1)
    非同期ライフサイクル（PENDING -> SUCCESS/FAILURE/REJECTED/UNKNOWN）を管理する
    """
    def __init__(
        self,
        efp: BusinessInput,
        f_pred: InterpretationPrediction,
        candidate_knowledge: Optional[str] = None,
        is_authoritative: bool = False,
        is_canary: bool = False,
        frozen_node_snapshot: Optional[Any] = None,
        frozen_context: Optional[FrozenInterpretationContext] = None,
    ):
        self.efp = efp
        self.f_pred = f_pred
        self.candidate_knowledge = candidate_knowledge
        self.is_authoritative = is_authoritative
        self.is_canary = is_canary
        self.frozen_node_snapshot = frozen_node_snapshot
        self.frozen_context = frozen_context
        self.status = CaseStatus.PENDING
        self.efp_prime: Optional[FeedbackResult] = None
        self.f_prime: Optional[SubsequentInterpretation] = None  # 後続作用解釈 F'
        self.e_prediction: Optional[float] = None
        self.e_input: Optional[float] = None
        self.dispatched_at = datetime.utcnow().isoformat()
        self.resolved_at: Optional[str] = None

    def record_feedback(self, feedback: FeedbackResult) -> Tuple[float, float]:
        """
        後続結果 EFP' を受領し、更新前の同一 M_B 前提 (FrozenInterpretationContext) で真に再解釈して F' を導出。
        F と F' の差分 E = Δ(F, F') を確定する。
        """
        self.efp_prime = feedback
        self.resolved_at = datetime.utcnow().isoformat()

        # 1. 更新前の同一構造 (frozen_context) による後続結果 EFP' の真の再解釈
        reinterpreted_pred = None
        if self.frozen_context:
            prime_text = (
                feedback.new_knowledge_provided
                or feedback.actual_response_text
                or feedback.feedback_comment
                or self.efp.query_text
            )
            efp_prime_input = BusinessInput(
                ticket_id=f"{self.efp.ticket_id}_prime",
                user_id=self.efp.user_id,
                category=self.efp.category,
                query_text=prime_text,
                metadata=self.efp.metadata,
            )
            reinterpreted_pred = self.frozen_context.interpret_efp(efp_prime_input)

        # 帰結状態と確信度の判定
        if feedback.human_rejected:
            actual_status = CaseStatus.REJECTED
            actual_outcome = "rejected"
            confidence_prime = 0.0
            explanation = "F' 事後解釈: 権限者による差し戻し（更新前モデルの解釈境界が破断）"
        elif not feedback.user_resolved:
            actual_status = CaseStatus.FAILURE
            actual_outcome = "unresolved"
            base_conf = reinterpreted_pred.confidence if reinterpreted_pred else getattr(self.frozen_node_snapshot, "confidence", self.f_pred.confidence)
            confidence_prime = max(0.0, base_conf - 0.4)
            explanation = "F' 事後解釈: ユーザー未解決（更新前モデルの予測回答が不適合）"
        else:
            actual_status = CaseStatus.SUCCESS
            actual_outcome = "resolve"
            base_conf = reinterpreted_pred.confidence if reinterpreted_pred else getattr(self.frozen_node_snapshot, "confidence", self.f_pred.confidence)
            confidence_prime = min(1.0, base_conf + 0.05)
            explanation = "F' 事後解釈: ユーザー解決完了（更新前モデルの予測と外界帰結が整合）"

        self.status = actual_status

        # 2. 予測 F と後続解釈 F' の差分 Δ_pred(F, F') の算出 (E_prediction)
        e_pred = 0.0
        if actual_status == CaseStatus.REJECTED:
            e_pred += 1.5
        elif actual_status == CaseStatus.FAILURE:
            e_pred += 1.0

        # 期待帰結と事後解釈帰結の不整合ペナルティ
        if self.f_pred.expected_outcome in ("resolve", "resolved") and actual_outcome not in ("resolve", "resolved"):
            e_pred += 0.5

        # 確信度乖離ペナルティ |F.confidence - F'.confidence_prime|
        conf_gap = max(0.0, self.f_pred.confidence - confidence_prime)
        e_pred += 0.2 * conf_gap

        # 更新前 M_B を通した事後再解釈でノード境界破断 (破断面の露呈) が検知された場合のペナルティ
        if reinterpreted_pred and reinterpreted_pred.matched_node_id != self.f_pred.matched_node_id:
            if actual_status in (CaseStatus.FAILURE, CaseStatus.REJECTED):
                e_pred += 0.3
                explanation += f" (更新前 M_B 再解釈でのノード境界変異検知: {self.f_pred.matched_node_id} -> {reinterpreted_pred.matched_node_id})"

        # 3. 入力素流圧 EFP と事後素流圧 EFP' の入力境界差分 Δ_input(EFP, EFP') の算出 (E_input)
        e_input = 0.0
        if len(self.efp.query_text.strip()) < 5:
            e_input += 0.5  # 入力境界の狭窄・情報不足
        if not self.efp.category:
            e_input += 0.3  # ドメイン境界未確定
        if feedback.new_knowledge_provided:
            e_input += 0.4  # 事後入力で新知識が補足されたことによる入力欠落の顕在化

        matched_nid = reinterpreted_pred.matched_node_id if reinterpreted_pred else self.f_pred.matched_node_id

        # 4. F' の確定保存 (T0 Core Requirement C3, C4)
        self.f_prime = SubsequentInterpretation(
            actual_status=actual_status,
            actual_outcome=actual_outcome,
            confidence_prime=round(confidence_prime, 4),
            matched_node_id=matched_nid,
            e_prediction_delta=round(e_pred, 4),
            e_input_delta=round(e_input, 4),
            explanation=explanation,
        )

        self.e_prediction = round(e_pred, 4)
        self.e_input = round(e_input, 4)
        return self.e_prediction, self.e_input

    def mark_unknown(self) -> Tuple[float, float]:
        """タイムアウト等の理由で結果が回収不能になった場合 (F' は UNKNOWN として解釈)"""
        self.status = CaseStatus.UNKNOWN
        self.resolved_at = datetime.utcnow().isoformat()
        e_pred = 0.2
        e_input = 0.1
        self.f_prime = SubsequentInterpretation(
            actual_status=CaseStatus.UNKNOWN,
            actual_outcome="unresolved",
            confidence_prime=0.2,
            matched_node_id=self.f_pred.matched_node_id,
            e_prediction_delta=e_pred,
            e_input_delta=e_input,
            explanation="タイムアウトにより事後結果回収不能：未回収関係 ξ として不確実性熱を残存",
        )
        self.e_prediction = e_pred
        self.e_input = e_input
        return self.e_prediction, self.e_input
