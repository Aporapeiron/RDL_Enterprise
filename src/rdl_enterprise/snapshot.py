from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Tuple

@dataclass
class BusinessInput:
    ticket_id: str
    user_id: str
    category: Optional[str]
    query_text: str
    metadata: Dict[str, Any] = field(default_factory=dict)


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
    human_approved: bool              # 先輩/管理者が承認したか
    human_rejected: bool              # 先輩/管理者が差し戻したか
    actual_response_text: Optional[str] = None
    feedback_comment: Optional[str] = None
    new_knowledge_provided: Optional[str] = None


class CaseSnapshot:
    """
    同一更新前 M_B に基づく F と F' の差分 E 算出器
    """
    def __init__(self, efp: BusinessInput, f_pred: InterpretationPrediction):
        self.efp = efp
        self.f_pred = f_pred
        self.efp_prime: Optional[FeedbackResult] = None
        self.f_prime: Optional[Dict[str, Any]] = None

    def record_feedback(self, feedback: FeedbackResult) -> Tuple[float, float]:
        """
        後続結果 EFP' を受けて、同一の更新前前提で F' を導出し、
        差分 E = (E_prediction, E_input) を計算して返す。
        """
        self.efp_prime = feedback

        e_pred = 0.0
        e_input = 0.0

        # 入力不整合の評価 (E_input)
        if len(self.efp.query_text.strip()) < 5:
            e_input += 0.5  # 入力不足
        if not self.efp.category:
            e_input += 0.3  # カテゴリ未指定

        # 事前予測 F と 事後結果 F' の乖離評価 (E_prediction)
        # 1. 差し戻しが発生した場合（大不整合）
        if feedback.human_rejected:
            e_pred += 1.5
        # 2. ユーザーが解決しなかった場合
        elif not feedback.user_resolved:
            e_pred += 1.0
        # 3. 解決かつ承認された場合
        elif feedback.user_resolved and feedback.human_approved:
            # 予測通り解決なら誤差ゼロ（むしろマイナス＝整合）
            e_pred = 0.0

        # 4. 「解決するはず」と予測していたのに失敗した場合のペナルティ
        if self.f_pred.expected_outcome == "resolve" and not feedback.user_resolved:
            e_pred += 0.5

        return e_pred, e_input
