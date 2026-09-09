"""
RDL Simulation Harness - Agent System
複数主体（User, Authority, Environment）およびペルソナによる有限観測生成モジュール。
"""

import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Persona:
    """
    利用者の行動特性・有限性を規定するペルソナ。
    世界から絶対の真理を受け取るのではなく、
    有限な主体から有限な観測(EFP')が生起する力学を再現する。
    """
    name: str
    cohort: str = "general"               # 属するコホート (newbie, veteran, impatient, security等)
    expertise: float = 0.5                # ITリテラシー・理解度 (0.0=素人, 1.0=熟練者)
    patience: float = 0.7                 # 忍耐度 (低いと未解決時に放置しやすく、TIMEOUT/UNKNOWN化を誘発)
    feedback_reliability: float = 0.9    # 報告の正確性 (低いと解決したのに未解決と誤認したり逆を報告)
    ambiguity: float = 0.2                # 表現の曖昧さ・主観性 (高いとクエリやフィードバックにノイズ混入)
    seed: Optional[int] = None
    rng: random.Random = field(init=False, repr=False)

    def __post_init__(self):
        self.rng = random.Random(self.seed if self.seed is not None else 42)

    def set_rng(self, rng: random.Random) -> None:
        self.rng = rng

    def evaluate_response(
        self,
        query: str,
        response_text: str,
        oracle_truth: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        AIからの回答 response_text を受け取り、自らのペルソナに基づいた
        主観的有限観測 (FeedbackResult) を生成する。
        """
        # 1. 回答内容の基本的合致判定
        # oracle_truth（現行世界の正解キーワード等）が指定されている場合は照合
        is_actually_correct = True
        if oracle_truth:
            is_actually_correct = (oracle_truth.lower() in response_text.lower())

        # 2. 忍耐度による放置（未返信/無反応）の判定
        # 忍耐度が低く、かつ回答がピンとこない場合は放置（フィードバック送信せず放置）
        will_abandon = False
        if not is_actually_correct and self.rng.random() > self.patience:
            will_abandon = True

        if will_abandon:
            return {
                "abandoned": True,
                "user_resolved": None,
                "complaint": False,
                "feedback_text": "（無反応・放置）",
                "delay_ticks": 9999,  # 実質無限
            }

        # 3. フィードバック信頼性による認識ズレ
        perceived_success = is_actually_correct
        if self.rng.random() > self.feedback_reliability:
            # 誤認発生: 正しいのに失敗と判定、あるいは失敗なのに成功と勘違い
            perceived_success = not is_actually_correct

        # 4. 苦情（Complaint）判定: 忍耐度が低く、かつ不満（失敗）の時に発熱源となる苦情
        complaint = False
        if not perceived_success:
            if self.rng.random() > self.patience * 0.8:
                complaint = True

        # 5. 返答までの遅延 (Tick数: 忍耐度やペルソナによって異なる)
        delay_ticks = int(max(1, (1.0 - self.patience) * 4 + self.rng.randint(1, 3)))

        return {
            "abandoned": False,
            "user_resolved": perceived_success,
            "complaint": complaint,
            "feedback_text": "解決しました" if perceived_success else ("分かりにくい/解決しない" if not complaint else "全然動かない！使えない！"),
            "delay_ticks": delay_ticks,
        }


class SimAgent:
    """エージェントの基底クラス"""
    def __init__(self, agent_id: str, role: str):
        self.agent_id = agent_id
        self.role = role

    def set_rng(self, rng: random.Random) -> None:
        pass

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.agent_id} role={self.role}>"


class UserAgent(SimAgent):
    """
    問い合わせを発行し、AIの回答を主観的に評価して有限フィードバックを返す利用者エージェント。
    """
    def __init__(self, agent_id: str, persona: Persona):
        super().__init__(agent_id=agent_id, role="user")
        self.persona = persona
        self.ticket_history: List[Dict[str, Any]] = []

    def set_rng(self, rng: random.Random) -> None:
        self.persona.set_rng(rng)

    def create_query(self, base_query: str, category: str = "general") -> Dict[str, Any]:
        """
        ペルソナの曖昧さ (ambiguity) に応じたノイズや表現揺れを付与したクエリを生成。
        """
        query = base_query
        if self.persona.ambiguity > 0.5:
            perturbations = [
                f"{query} ...なんですけどどうすればいい？",
                f"すみません、{query}の件で困ってます",
                f"{query} お願いします",
            ]
            query = self.persona.rng.choice(perturbations)
        return {
            "user_id": self.agent_id,
            "cohort": self.persona.cohort,
            "category": category,
            "query_text": query,
            "persona_name": self.persona.name,
        }

    def generate_feedback(
        self,
        query: str,
        response_text: str,
        oracle_truth: Optional[str] = None,
    ) -> Dict[str, Any]:
        """ペルソナを通じた有限観測を生成"""
        eval_result = self.persona.evaluate_response(query, response_text, oracle_truth)
        eval_result["user_id"] = self.agent_id
        eval_result["cohort"] = self.persona.cohort
        return eval_result


class AuthorityAgent(SimAgent):
    """
    組織の方針・指示（Authority Commitment）を注入する権威エージェント。
    （例: 先輩社員、情シス管理者、セキュリティ責任者）
    """
    def __init__(
        self,
        agent_id: str,
        role: str,
        domain_scopes: Optional[List[str]] = None,
        capability: str = "authoritative_injection",
    ):
        super().__init__(agent_id=agent_id, role=role)
        self.domain_scopes = domain_scopes or []
        self.capability = capability

    def create_directive(
        self,
        domain: str,
        trigger_pattern: Dict[str, Any],
        action_template: Dict[str, Any],
        authority_level: str = "policy",
    ) -> Dict[str, Any]:
        """正式な方針注入ディレクティブを作成"""
        return {
            "verifier_id": self.agent_id,
            "role": self.role,
            "domain": domain,
            "domain_scopes": self.domain_scopes,
            "capability": self.capability,
            "trigger_pattern": trigger_pattern,
            "action_template": action_template,
            "authority_level": authority_level,
        }


class EnvironmentAgent(SimAgent):
    """
    制度変更、ツール移行、ネットワーク障害等の環境変化を管轄するエージェント。
    """
    def __init__(self, agent_id: str = "env_world"):
        super().__init__(agent_id=agent_id, role="environment")
        self.active_conditions: Dict[str, Any] = {}

    def set_condition(self, key: str, value: Any) -> None:
        self.active_conditions[key] = value

    def get_condition(self, key: str, default: Any = None) -> Any:
        return self.active_conditions.get(key, default)
