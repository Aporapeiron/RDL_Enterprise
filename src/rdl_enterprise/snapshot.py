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


import hashlib
import json

@dataclass
class LLMBridgeIdentity:
    """推論器 (LLM Bridge) の同一性監査用アイデンティティ (T0 SPEC 4: 同一解釈条件保証)"""
    model_name: str = "mock-llm"
    temperature: float = 0.0
    system_prompt_version: str = "v1"
    config_hash: str = "default"

    @classmethod
    def from_bridge(cls, bridge: Optional[Any]) -> "LLMBridgeIdentity":
        if bridge is None:
            return cls(model_name="none", config_hash="none")
        m_name = getattr(bridge, "model_name", getattr(bridge, "model", "generic-llm"))
        temp = float(getattr(bridge, "temperature", 0.0))
        sp_ver = getattr(bridge, "system_prompt_version", "v1")
        cfg_str = f"{m_name}:{temp}:{sp_ver}"
        c_hash = hashlib.sha256(cfg_str.encode("utf-8")).hexdigest()[:16]
        return cls(
            model_name=str(m_name),
            temperature=temp,
            system_prompt_version=str(sp_ver),
            config_hash=c_hash,
        )


@dataclass
class FrozenInterpretationContext:
    """
    更新前の同一 M_B および推論環境（キャッシュ・設定・モデルIdentity）の完全凍結スナップショット (T0 SPEC 4, 6.1)
    F (事前予測) と F' (事後解釈) を厳密に同一の前提・同一の解釈条件のもとで形成するための暗号論的保証構造。
    """
    mb_version: str
    mb_content_hash: str
    frozen_mb: Any                                        # MBGraph (Deep Freeze済み)
    target_domain: Optional[str] = None
    llm_bridge: Optional[Any] = None
    initial_level0_cache: Dict[Tuple[str, str], str] = field(default_factory=dict)
    cascade_config: Optional[Any] = None                  # CascadeConfig
    llm_identity: Optional[LLMBridgeIdentity] = None
    _context_cascade: Optional[Any] = field(default=None, repr=False, compare=False)

    def get_or_create_cascade(self) -> Any:
        """同一コンテキスト内で共有される InterpCascade インスタンスを取得"""
        if self._context_cascade is None:
            from .cascade import InterpCascade, CascadeConfig
            cfg = self.cascade_config if self.cascade_config is not None else CascadeConfig()
            self._context_cascade = InterpCascade(
                mb_graph=self.frozen_mb,
                llm_bridge=self.llm_bridge,
                config=cfg,
                initial_cache=self.initial_level0_cache,
            )
        return self._context_cascade

    def interpret_efp(self, efp: BusinessInput) -> InterpretationPrediction:
        """更新前の同一 M_B 前提・同一解釈条件を用いて入力を解釈 (interp(M_B, EFP))"""
        # 1. グラフ変質（Identity Drift）の検証
        if hasattr(self.frozen_mb, "content_hash"):
            current_h = self.frozen_mb.content_hash()
            if self.mb_content_hash != "unknown" and current_h != self.mb_content_hash:
                raise RuntimeError(f"FrozenInterpretationContext の変質を検知 (Identity Drift: {current_h} != {self.mb_content_hash})")

        # 2. 推論器 (LLM Bridge) の同一性検証
        if self.llm_identity is not None and self.llm_bridge is not None:
            current_id = LLMBridgeIdentity.from_bridge(self.llm_bridge)
            if current_id.config_hash != self.llm_identity.config_hash:
                raise RuntimeError(
                    f"LLM Identity Drift を検知 (推論器構成の変質: {current_id} != {self.llm_identity})"
                )

        cascade = self.get_or_create_cascade()
        return cascade.interpret(efp)


class EFPPrimeAdapter:
    """
    FeedbackResult (外界事後結果) を情報損失なく構造化された事後素流圧 EFP' (BusinessInput) へ適応
    """
    @staticmethod
    def adapt(efp: BusinessInput, feedback: FeedbackResult) -> BusinessInput:
        prime_ticket_id = f"{efp.ticket_id}_prime"
        # 元クエリの文脈と、事後提供された新情報・訂正・コメントを可逆的に統合
        text_components = [efp.query_text]
        if feedback.new_knowledge_provided:
            text_components.append(f"【新知識・追加指示】{feedback.new_knowledge_provided}")
        if feedback.actual_response_text and feedback.actual_response_text != efp.query_text:
            text_components.append(f"【実施回答】{feedback.actual_response_text}")
        if feedback.feedback_comment:
            text_components.append(f"【フィードバック】{feedback.feedback_comment}")

        combined_text = "\n".join(text_components)

        metadata = dict(efp.metadata)
        metadata.update({
            "is_efp_prime": True,
            "original_query": efp.query_text,
            "user_resolved": feedback.user_resolved,
            "human_approved": feedback.human_approved,
            "human_rejected": feedback.human_rejected,
            "actual_response_text": feedback.actual_response_text,
            "feedback_comment": feedback.feedback_comment,
            "new_knowledge_provided": feedback.new_knowledge_provided,
        })

        return BusinessInput(
            ticket_id=prime_ticket_id,
            user_id=efp.user_id,
            category=efp.category,
            query_text=combined_text,
            metadata=metadata,
        )


@dataclass
class ObservedOutcome:
    """
    外界で実際に観測された事実 O' (T0: 外界帰結)
    F' (更新前M_Bによる解釈) と厳密に分離される外界の観測結果
    """
    status: CaseStatus                         # SUCCESS | FAILURE | REJECTED | UNKNOWN
    outcome: str                               # "resolve" | "unresolved" | "rejected"
    user_resolved: bool
    human_approved: bool = False
    human_rejected: bool = False
    feedback_comment: Optional[str] = None
    actual_response_text: Optional[str] = None
    observed_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class SubsequentInterpretation:
    """
    純粋な後続作用解釈 F' (T0 SPEC 4, 6.1)
    後続する EFP' を、更新前の同一 M_B が解釈して形成した作用予測情報 interp(M_B, EFP')
    """
    action_type: str                           # 解釈されたアクション ("direct_reply" | "tool_call" | "ask_human" | "delegate")
    content: str                               # 作用内容
    confidence_prime: float                    # 事後入力受領後の更新前構造における確信度
    matched_node_id: Optional[str]             # 更新前前提での該当ノードID
    cost_tier: int                             # 0: ローカル最小, 1: ルール, 2: 局所推論, 3: 外部LLM
    domain: Optional[str]                      # 解釈ドメイン境界
    expected_outcome: str                      # "resolve" | "need_input" | "escalate"
    e_prediction_delta: float                  # F と F' および O' の間の総合予測不整合
    e_input_delta: float                       # 入力境界の不整合 Δ_input(EFP, EFP')
    actual_status: Optional[CaseStatus] = None # 後方互換性プロパティ (ObservedOutcome.status と連動)
    actual_outcome: Optional[str] = None       # 後方互換性プロパティ (ObservedOutcome.outcome と連動)
    explanation: str = ""                      # 解釈根拠の記録
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
        self.observed_outcome: Optional[ObservedOutcome] = None # 外界観測事実 O'
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
        # 1. 外界観測事実 O' (ObservedOutcome) の確定
        if feedback.human_rejected:
            obs_status = CaseStatus.REJECTED
            obs_outcome = "rejected"
        elif not feedback.user_resolved:
            obs_status = CaseStatus.FAILURE
            obs_outcome = "unresolved"
        else:
            obs_status = CaseStatus.SUCCESS
            obs_outcome = "resolve"

        self.status = obs_status
        self.observed_outcome = ObservedOutcome(
            status=obs_status,
            outcome=obs_outcome,
            user_resolved=feedback.user_resolved,
            human_approved=feedback.human_approved,
            human_rejected=feedback.human_rejected,
            feedback_comment=feedback.feedback_comment,
            actual_response_text=feedback.actual_response_text,
        )

        # 2. 更新前の同一構造・同一解釈条件 (frozen_context) による後続結果 EFP' の真の再解釈 (F')
        efp_prime_input = EFPPrimeAdapter.adapt(self.efp, feedback)
        reinterpreted_pred = None
        if self.frozen_context:
            reinterpreted_pred = self.frozen_context.interpret_efp(efp_prime_input)

        # 純粋な事後予測 F' の属性決定
        if reinterpreted_pred:
            f_prime_action = reinterpreted_pred.action_type
            f_prime_content = reinterpreted_pred.content
            base_conf = reinterpreted_pred.confidence
            matched_nid = reinterpreted_pred.matched_node_id
            cost_tier_prime = reinterpreted_pred.cost_tier
            domain_prime = reinterpreted_pred.domain
            expected_outcome_prime = reinterpreted_pred.expected_outcome
        else:
            base_node = self.frozen_node_snapshot
            f_prime_action = getattr(base_node, "action_template", {}).get("type", self.f_pred.action_type) if base_node else self.f_pred.action_type
            f_prime_content = getattr(base_node, "action_template", {}).get("payload", self.f_pred.content) if base_node else self.f_pred.content
            base_conf = getattr(base_node, "confidence", self.f_pred.confidence) if base_node else self.f_pred.confidence
            matched_nid = self.f_pred.matched_node_id
            cost_tier_prime = self.f_pred.cost_tier
            domain_prime = self.f_pred.domain
            expected_outcome_prime = self.f_pred.expected_outcome

        # 外界帰結 O' と連動した更新前前提での確信度評価 (confidence_prime)
        if obs_status == CaseStatus.REJECTED:
            confidence_prime = 0.0
        elif obs_status == CaseStatus.FAILURE:
            confidence_prime = max(0.0, base_conf - 0.4)
        else:
            confidence_prime = min(1.0, base_conf + 0.05)

        # 3. 総合誤差 E = Δ(F, F', O') の算出
        # (a) 外界帰結不整合 Δ_outcome(F, O')
        delta_outcome = 0.0
        if obs_status == CaseStatus.REJECTED:
            delta_outcome += 1.5
            explanation = "F' 事後解釈: 権限者による差し戻し（更新前モデルの解釈境界が破断）"
        elif obs_status == CaseStatus.FAILURE:
            delta_outcome += 1.0
            explanation = "F' 事後解釈: ユーザー未解決（更新前モデルの予測回答が不適合）"
        else:
            explanation = "F' 事後解釈: ユーザー解決完了（更新前モデルの予測と外界帰結が整合）"

        if self.f_pred.expected_outcome in ("resolve", "resolved") and obs_outcome not in ("resolve", "resolved"):
            delta_outcome += 0.5

        # (b) 解釈器内部の予測差分 Δ_pred(F, F') (確信度乖離・ノード境界変異)
        delta_pred = 0.0
        conf_gap = max(0.0, self.f_pred.confidence - confidence_prime)
        delta_pred += 0.2 * conf_gap

        if reinterpreted_pred and reinterpreted_pred.matched_node_id != self.f_pred.matched_node_id:
            if obs_status in (CaseStatus.FAILURE, CaseStatus.REJECTED):
                delta_pred += 0.3
                explanation += f" (更新前 M_B 再解釈でのノード境界変異検知: {self.f_pred.matched_node_id} -> {reinterpreted_pred.matched_node_id})"

        e_pred = delta_outcome + delta_pred

        # (c) 入力素流圧境界差分 Δ_input(EFP, EFP') (E_input)
        e_input = 0.0
        if len(self.efp.query_text.strip()) < 5:
            e_input += 0.5
        if not self.efp.category:
            e_input += 0.3
        if feedback.new_knowledge_provided:
            e_input += 0.4

        # 4. 純粋な F' (SubsequentInterpretation) の確定保存
        self.f_prime = SubsequentInterpretation(
            action_type=f_prime_action,
            content=f_prime_content,
            confidence_prime=round(confidence_prime, 4),
            matched_node_id=matched_nid,
            cost_tier=cost_tier_prime,
            domain=domain_prime,
            expected_outcome=expected_outcome_prime,
            e_prediction_delta=round(e_pred, 4),
            e_input_delta=round(e_input, 4),
            actual_status=obs_status,    # 後方互換性
            actual_outcome=obs_outcome,  # 後方互換性
            explanation=explanation,
        )

        self.e_prediction = round(e_pred, 4)
        self.e_input = round(e_input, 4)
        return self.e_prediction, self.e_input

    def mark_unknown(self) -> Tuple[float, float]:
        """タイムアウト等の理由で結果が回収不能になった場合 (F' は UNKNOWN として解釈)"""
        self.status = CaseStatus.UNKNOWN
        self.resolved_at = datetime.utcnow().isoformat()
        self.observed_outcome = ObservedOutcome(
            status=CaseStatus.UNKNOWN,
            outcome="unresolved",
            user_resolved=False,
            feedback_comment="タイムアウト",
        )
        e_pred = 0.2
        e_input = 0.1
        self.f_prime = SubsequentInterpretation(
            action_type=self.f_pred.action_type,
            content=self.f_pred.content,
            confidence_prime=0.2,
            matched_node_id=self.f_pred.matched_node_id,
            cost_tier=self.f_pred.cost_tier,
            domain=self.f_pred.domain,
            expected_outcome="escalate",
            e_prediction_delta=e_pred,
            e_input_delta=e_input,
            actual_status=CaseStatus.UNKNOWN,
            actual_outcome="unresolved",
            explanation="タイムアウトにより事後結果回収不能：未回収関係 ξ として不確実性熱を残存",
        )
        self.e_prediction = e_pred
        self.e_input = e_input
        return self.e_prediction, self.e_input
