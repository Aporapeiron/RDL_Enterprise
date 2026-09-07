from typing import Optional, Dict, Any
from .mb_graph import MBNode
from .snapshot import BusinessInput, InterpretationPrediction

class HumanQuery:
    """
    人間問い合わせ（HITL: Human-in-the-Loop）制御ゲート
    κゲート判定、権限境界チェック、エスカレーション判定を司る
    """
    def __init__(self, kappa_threshold: float = 0.2, min_confidence: float = 0.4):
        self.kappa_threshold = kappa_threshold
        self.min_confidence = min_confidence

    def evaluate(self, efp: BusinessInput, pred: InterpretationPrediction, node: Optional[MBNode]) -> Dict[str, Any]:
        """
        人間に確認・委譲すべきかどうかの判定
        Returns: {
            "must_ask": bool,
            "reason": str,
            "query_type": "confirm_auto" | "ask_guidance" | "permission_request"
        }
        """
        # 1. もともと推論結果が ask_human の場合
        if pred.action_type == "ask_human":
            return {
                "must_ask": True,
                "reason": "推論層での確信度不足 / 未知案件",
                "query_type": "ask_guidance",
            }

        # 2. 権限境界 (B制約) による判定
        if node:
            if node.authority_level == "human_only":
                return {
                    "must_ask": True,
                    "reason": "権限境界: 人間のみ実行可能 (human_only)",
                    "query_type": "permission_request",
                }
            if node.authority_level == "require_approval":
                return {
                    "must_ask": True,
                    "reason": "権限境界: 実行前に人間承認が必要 (require_approval)",
                    "query_type": "confirm_auto",
                }

        # 3. 確信度チェック
        if pred.confidence < self.min_confidence:
            return {
                "must_ask": True,
                "reason": f"確信度不足 ({pred.confidence:.2f} < {self.min_confidence})",
                "query_type": "ask_guidance",
            }

        # 4. κ ゲートチェック (自己修正不能警告)
        if node:
            k = node.kappa()
            if k < self.kappa_threshold:
                # 慣性が固まりすぎているため、念のためのレビューを推奨
                return {
                    "must_ask": True,
                    "reason": f"κゲート警告: 慣性固着 (κ={k:.3f} < {self.kappa_threshold})",
                    "query_type": "confirm_auto",
                }

        # すべてクリア：自動処理可能
        return {
            "must_ask": False,
            "reason": "自動処理基準適合",
            "query_type": "none",
        }
