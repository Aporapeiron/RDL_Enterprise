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


class CaseSnapshot:
    """
    同一更新前 M_B に基づく F と F' の差分 E 算出器
    非同期ライフサイクル（PENDING -> SUCCESS/FAILURE/REJECTED/UNKNOWN）を管理する
    """
    def __init__(self, efp: BusinessInput, f_pred: InterpretationPrediction):
        self.efp = efp
        self.f_pred = f_pred
        self.status = CaseStatus.PENDING
        self.efp_prime: Optional[FeedbackResult] = None
        self.e_prediction: Optional[float] = None
        self.e_input: Optional[float] = None
        self.dispatched_at = datetime.utcnow().isoformat()
        self.resolved_at: Optional[str] = None

    def record_feedback(self, feedback: FeedbackResult) -> Tuple[float, float]:
        """
        後続結果 EFP' を受領し、同一の更新前前提で F' を導出して
        差分 E = (E_prediction, E_input) を確定する。
        """
        self.efp_prime = feedback
        self.resolved_at = datetime.utcnow().isoformat()

        e_pred = 0.0
        e_input = 0.0

        # 入力不整合の評価 (E_input)
        if len(self.efp.query_text.strip()) < 5:
            e_input += 0.5  # 入力不足
        if not self.efp.category:
            e_input += 0.3  # カテゴリ未指定

        # 事後結果に基づくステータス判定と予測誤差 (E_prediction)
        if feedback.human_rejected:
            self.status = CaseStatus.REJECTED
            e_pred += 1.5
        elif not feedback.user_resolved:
            self.status = CaseStatus.FAILURE
            e_pred += 1.0
        else:
            self.status = CaseStatus.SUCCESS
            e_pred = 0.0

        # 「解決するはず」と予測していたのに失敗した場合のペナルティ
        if self.f_pred.expected_outcome == "resolve" and self.status in (CaseStatus.FAILURE, CaseStatus.REJECTED):
            e_pred += 0.5

        self.e_prediction = e_pred
        self.e_input = e_input
        return e_pred, e_input

    def mark_unknown(self) -> Tuple[float, float]:
        """タイムアウト等の理由で結果が回収不能になった場合"""
        self.status = CaseStatus.UNKNOWN
        self.resolved_at = datetime.utcnow().isoformat()
        # 軽微な不確実性熱
        self.e_prediction = 0.2
        self.e_input = 0.1
        return self.e_prediction, self.e_input
