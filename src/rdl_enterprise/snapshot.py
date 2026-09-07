from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Tuple
from enum import Enum
from datetime import datetime
import copy

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
class RelationProvenance:
    """
    後続作用 EFP' や関係入力の来歴・制度的位置付け (BASE v2.0 §4.2)
    「誰が・どの関係位置から・何を・いつ・どの媒体/制度経路を通して報告したか」を構造化。
    """
    source_type: str = "user"           # "user" | "senior" | "admin" | "oracle" | "audit" | "system" | "human_feedback"
    authority_level: str = "auto"       # "auto" | "require_approval" | "human_only" | "unknown"
    observed_at: Optional[datetime] = None
    source_id: Optional[str] = None     # 報告者・システムID
    channel: str = "standard"           # "standard" | "official_doc" | "audit_log" | "admin_override" | "feedback"
    is_authoritative: bool = False      # 制度的公式記録・オラクル決定か
    authority_scope: Optional[str] = None # 管轄ドメイン・スコープ（例: "workflow", "hr", "security", "*"）
    claim_type: str = "general"         # 言明タイプ（"fact" | "rule" | "judgment" | "policy" | "general"）
    relation_type: Optional[str] = None # 関係性質（時間減衰半減期を決定: "fact" | "policy" | "rule" | "ephemeral"）


@dataclass
class FeedbackResult:
    """事後結果 EFP' (外界反作用および後続拘束)"""
    user_resolved: bool                         # ユーザーが解決したか
    human_approved: bool = False                # 先輩/管理者が承認したか
    human_rejected: bool = False                # 先輩/管理者が差し戻したか
    actual_response_text: Optional[str] = None
    feedback_comment: Optional[str] = None
    new_knowledge_provided: Optional[str] = None
    correction_content: Optional[str] = None    # 修正・是正内容（明示的対向命題）
    provenance: Optional[RelationProvenance] = None  # 後続関係の来歴・権限

    def __post_init__(self):
        # provenance 未指定時の安全なフォールバック (公理 B5: 権限の捏造禁止)
        # 人間による差し戻し/承認であっても、未指定時に勝手に senior や require_approval を名乗らせず、
        # 未特定の人間フィードバック (source_type="human_feedback", authority_level="unknown") として扱う。
        if self.provenance is None:
            if self.human_rejected or self.human_approved:
                self.provenance = RelationProvenance(
                    source_type="human_feedback",
                    authority_level="unknown",
                    channel="feedback",
                    is_authoritative=False,
                )
            else:
                self.provenance = RelationProvenance(
                    source_type="user",
                    authority_level="auto",
                    channel="standard",
                    is_authoritative=False,
                )


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
    更新前の同一 M_B および推論環境（キャッシュ・設定・モデルIdentity・関係拘束条件）の
    完全凍結スナップショット (T0 SPEC 4, 6.1 / BASE v2.0 §4.2)

    F (事前予測) と F' (事後解釈) を厳密に同一の初期前提・同一の解釈条件のもとで独立形成するための暗号論的保証構造。

    【BASE v2.0 整合】関係拘束強度は「時点」によって変わる。
    したがって、F と F' を同一の「関係拘束評価時刻」のもとで解釈するため、
    constraint_evaluation_time を dispatch 時に凍結し、F' 形成時にも同じ時刻を使用する。
    """
    mb_version: str
    mb_content_hash: str
    frozen_mb: Any                                        # MBGraph (Deep Freeze済み)
    target_domain: Optional[str] = None
    llm_bridge: Optional[Any] = None
    initial_level0_cache: Dict[Tuple[str, str], str] = field(default_factory=dict)
    cascade_config: Optional[Any] = None                  # CascadeConfig
    llm_identity: Optional[LLMBridgeIdentity] = None
    # 関係拘束評価設定（凍結：F と F' の constraint_score が同一条件で算出されることを保証）
    constraint_config: Optional[Any] = None               # ConstraintConfig
    # 関係拘束評価時刻（凍結：freshness 等の時刻断面が F と F' で同一になることを保証）
    constraint_evaluation_time: Optional[Any] = None      # datetime
    context_hash: str = ""
    is_frozen: bool = False

    def __post_init__(self):
        # 外部変更を防ぐため辞書や設定をディープコピー
        import copy
        from datetime import datetime, timezone
        if not self.is_frozen:
            super().__setattr__("initial_level0_cache", dict(self.initial_level0_cache))
            if self.cascade_config is not None:
                super().__setattr__("cascade_config", copy.deepcopy(self.cascade_config))
            if self.constraint_config is not None:
                super().__setattr__("constraint_config", copy.deepcopy(self.constraint_config))
            # 関係拘束評価時刻を dispatch 時刻で凍結（未指定なら今この瞬間）
            if self.constraint_evaluation_time is None:
                super().__setattr__("constraint_evaluation_time", datetime.now(timezone.utc))
            if not self.context_hash:
                super().__setattr__("context_hash", self.compute_context_hash())
            super().__setattr__("is_frozen", True)

    def __setattr__(self, name: str, value: Any):
        if getattr(self, "is_frozen", False):
            raise RuntimeError(f"FrozenInterpretationContext は完全凍結(frozen)されています。属性 '{name}' の変更は禁止されています。")
        super().__setattr__(name, value)

    def compute_context_hash(self) -> str:
        """
        コンテキスト全体の構成要素から完全な暗号論的ハッシュを生成。
        M_B / キャッシュ / CascadeConfig / LLM / ドメイン に加え、
        ConstraintConfig と constraint_evaluation_time も包含する。
        (BASE v2.0 §4.2: 関係拘束強度は時点によって変化するため、評価時刻も同一性の要件)
        """
        from dataclasses import asdict
        cfg_dict = asdict(self.cascade_config) if (self.cascade_config and hasattr(self.cascade_config, "__dataclass_fields__")) else {}
        llm_dict = asdict(self.llm_identity) if (self.llm_identity and hasattr(self.llm_identity, "__dataclass_fields__")) else {}
        constraint_cfg_dict = asdict(self.constraint_config) if (self.constraint_config and hasattr(self.constraint_config, "__dataclass_fields__")) else {}
        # 評価時刻は ISO 文字列でハッシュに含める（秒単位で丸める：ミリ秒の微差を吸収）
        eval_time_str = ""
        if self.constraint_evaluation_time is not None:
            try:
                eval_time_str = self.constraint_evaluation_time.strftime("%Y-%m-%dT%H:%M:%S")
            except Exception:
                eval_time_str = str(self.constraint_evaluation_time)
        # キャッシュのソート済みシリアライズ
        sorted_cache = sorted([f"{k[0]}:{k[1]}->{v}" for k, v in self.initial_level0_cache.items()])
        payload = {
            "mb_version": self.mb_version,
            "mb_content_hash": self.mb_content_hash,
            "target_domain": self.target_domain or "",
            "cascade_config": cfg_dict,
            "llm_identity": llm_dict,
            "cache": sorted_cache,
            "constraint_config": constraint_cfg_dict,
            "constraint_evaluation_time": eval_time_str,
        }
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def create_isolated_cascade(self) -> Any:
        """
        F および F' が互いの計算によるキャッシュ変化に干渉されないよう、
        初期キャッシュスナップショット C0 から独立した InterpCascade インスタンスを生成。
        constraint_config と constraint_evaluation_time も伝播させ、
        F と F' が同一の「関係拘束評価条件」のもとで解釈されることを保証する。
        (T0 SPEC: F = interpret(EFP, C0, constraint_condition),
                  F' = interpret(EFP', C0, constraint_condition))
        """
        from .cascade import InterpCascade, CascadeConfig
        cfg = copy.deepcopy(self.cascade_config) if self.cascade_config is not None else CascadeConfig()
        return InterpCascade(
            mb_graph=self.frozen_mb,
            llm_bridge=self.llm_bridge,
            config=cfg,
            initial_cache=dict(self.initial_level0_cache),  # 常に初期 C0 のコピーを渡す
            constraint_config=copy.deepcopy(self.constraint_config) if self.constraint_config is not None else None,
            constraint_evaluation_time=self.constraint_evaluation_time,  # 凍結評価時刻を伝播
        )

    def get_or_create_cascade(self) -> Any:
        """後方互換用エイリアス（独立した InterpCascade インスタンスを生成）"""
        return self.create_isolated_cascade()

    def interpret_efp(self, efp: BusinessInput) -> InterpretationPrediction:
        """更新前の同一 M_B 前提・同一解釈条件（初期状態 C0）を用いて入力を独立解釈"""
        # 1. コンテキスト全体の変質（Context Drift）を総合検証
        cur_ctx_hash = self.compute_context_hash()
        if self.context_hash and cur_ctx_hash != self.context_hash:
            raise RuntimeError(f"FrozenInterpretationContext の変質を検知 (Context Drift: {cur_ctx_hash} != {self.context_hash})")

        # 2. グラフ変質の検証 (後方互換明示チェック)
        if hasattr(self.frozen_mb, "content_hash"):
            current_h = self.frozen_mb.content_hash()
            if self.mb_content_hash != "unknown" and current_h != self.mb_content_hash:
                raise RuntimeError(f"FrozenInterpretationContext の変質を検知 (Identity Drift: {current_h} != {self.mb_content_hash})")

        # 3. 推論器 (LLM Bridge) の同一性検証
        if self.llm_identity is not None and self.llm_bridge is not None:
            current_id = LLMBridgeIdentity.from_bridge(self.llm_bridge)
            if current_id.config_hash != self.llm_identity.config_hash:
                raise RuntimeError(
                    f"LLM Identity Drift を検知 (推論器構成の変質: {current_id} != {self.llm_identity})"
                )

        cascade = self.create_isolated_cascade()
        return cascade.interpret(efp)


class EFPPrimeAdapter:
    """
    FeedbackResult (外界事後結果) を情報損失なく構造化された事後素流圧 EFP' (BusinessInput) へ適応
    """
    @staticmethod
    def adapt(efp: BusinessInput, feedback: FeedbackResult) -> BusinessInput:
        from dataclasses import asdict
        prime_ticket_id = f"{efp.ticket_id}_prime"
        # 元クエリの文脈と、事後提供された新情報・是正指示・訂正・コメントを可逆的に統合
        text_components = [efp.query_text]
        if feedback.human_rejected:
            text_components.append("【事後帰結】差し戻し（人間判定により不適合）")
        elif not feedback.user_resolved:
            text_components.append("【事後帰結】未解決（ユーザー判定により不適合）")
        if feedback.correction_content:
            text_components.append(f"【是正・訂正指示】{feedback.correction_content}")
        if feedback.new_knowledge_provided:
            text_components.append(f"【新知識・追加指示】{feedback.new_knowledge_provided}")
        if feedback.actual_response_text and feedback.actual_response_text != efp.query_text:
            text_components.append(f"【実施回答】{feedback.actual_response_text}")
        if feedback.feedback_comment:
            text_components.append(f"【フィードバック】{feedback.feedback_comment}")

        combined_text = "\n".join(text_components)

        metadata = dict(efp.metadata)
        prov_dict = (
            asdict(feedback.provenance)
            if (feedback.provenance and hasattr(feedback.provenance, "__dataclass_fields__"))
            else feedback.provenance
        )
        metadata.update({
            "is_efp_prime": True,
            "original_query": efp.query_text,
            "user_resolved": feedback.user_resolved,
            "human_approved": feedback.human_approved,
            "human_rejected": feedback.human_rejected,
            "actual_response_text": feedback.actual_response_text,
            "feedback_comment": feedback.feedback_comment,
            "new_knowledge_provided": feedback.new_knowledge_provided,
            "correction_content": feedback.correction_content,
            "relation_provenance": prov_dict,
        })

        return BusinessInput(
            ticket_id=prime_ticket_id,
            user_id=efp.user_id,
            category=efp.category,
            query_text=combined_text,
            metadata=metadata,
        )


@dataclass
class OutcomeObservation:
    """
    後続して取得された帰結観測情報 (T0: EFP'の外界反作用シグナル)
    ※「世界そのもの」ではなく、有限な観測境界 B を通して取得された観測記録・報告。
    """
    status: CaseStatus                         # SUCCESS | FAILURE | REJECTED | UNKNOWN
    outcome: str                               # "resolve" | "unresolved" | "rejected"
    user_resolved: bool
    human_approved: bool = False
    human_rejected: bool = False
    feedback_comment: Optional[str] = None
    actual_response_text: Optional[str] = None
    observed_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


# 後方互換エイリアス
ObservedOutcome = OutcomeObservation


@dataclass
class SubsequentInterpretation:
    """
    純粋な後続作用解釈 F' (T0 SPEC 4, 6.1)
    後続する EFP' を、更新前の同一 M_B が初期状態 C0 から解釈して形成した純粋な作用予測情報 interp(M_B, EFP')
    外界観測ステータスによる直接書き換えを受けず、解釈器本来の推論結果を保持する。
    """
    action_type: str                           # 解釈されたアクション ("direct_reply" | "tool_call" | "ask_human" | "delegate")
    content: str                               # 作用内容
    confidence_prime: float                    # 事後入力受領後の更新前構造における純粋な確信度評価
    matched_node_id: Optional[str]             # 更新前前提での該当ノードID
    cost_tier: int                             # 0: ローカル最小, 1: ルール, 2: 局所推論, 3: 外部LLM
    domain: Optional[str]                      # 解釈ドメイン境界
    expected_outcome: str                      # "resolve" | "need_input" | "escalate"
    actual_status: Optional[CaseStatus] = None # 後方互換性プロパティ (OutcomeObservation.status と連動)
    actual_outcome: Optional[str] = None       # 後方互換性プロパティ (OutcomeObservation.outcome と連動)
    e_prediction_delta: float = 0.0            # 後方互換性プロパティ (CaseSnapshot.e_prediction と連動)
    e_input_delta: float = 0.0                 # 後方互換性プロパティ (CaseSnapshot.e_input と連動)
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
        self.f_prime: Optional[SubsequentInterpretation] = None  # 純粋な後続作用解釈 F'
        self.outcome_observation: Optional[OutcomeObservation] = None # 外界帰結観測情報
        self.e_prediction: Optional[float] = None
        self.e_input: Optional[float] = None
        self.dispatched_at = datetime.utcnow().isoformat()
        self.resolved_at: Optional[str] = None

    @property
    def observed_outcome(self) -> Optional[OutcomeObservation]:
        """後方互換プロパティ"""
        return self.outcome_observation

    @observed_outcome.setter
    def observed_outcome(self, val: Optional[OutcomeObservation]):
        self.outcome_observation = val

    def record_feedback(self, feedback: FeedbackResult) -> Tuple[float, float]:
        """
        後続結果 EFP' を受領し、更新前の同一 M_B 前提 (FrozenInterpretationContext) で真に再解釈して F' を導出。
        F と F' の差分 E = Δ(F, F') を確定する。
        """
        self.efp_prime = feedback
        self.resolved_at = datetime.utcnow().isoformat()

        # 1. 外界帰結観測情報 (OutcomeObservation) の確定 (EFP'の反作用成分)
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
        self.outcome_observation = OutcomeObservation(
            status=obs_status,
            outcome=obs_outcome,
            user_resolved=feedback.user_resolved,
            human_approved=feedback.human_approved,
            human_rejected=feedback.human_rejected,
            feedback_comment=feedback.feedback_comment,
            actual_response_text=feedback.actual_response_text,
        )

        # 2. 更新前の同一構造・同一初期キャッシュ条件 C0 (frozen_context) による後続結果 EFP' の真の再解釈 (F')
        efp_prime_input = EFPPrimeAdapter.adapt(self.efp, feedback)
        reinterpreted_pred = None
        if self.frozen_context:
            reinterpreted_pred = self.frozen_context.interpret_efp(efp_prime_input)

        # 純粋な事後予測 F' の属性決定 (外界観測ステータスによる確信度の直接書き換えを完全排除)
        if reinterpreted_pred:
            f_prime_action = reinterpreted_pred.action_type
            f_prime_content = reinterpreted_pred.content
            confidence_prime = reinterpreted_pred.confidence  # 純粋な再推論確信度
            matched_nid = reinterpreted_pred.matched_node_id
            cost_tier_prime = reinterpreted_pred.cost_tier
            domain_prime = reinterpreted_pred.domain
            expected_outcome_prime = reinterpreted_pred.expected_outcome
        else:
            base_node = self.frozen_node_snapshot
            f_prime_action = getattr(base_node, "action_template", {}).get("type", self.f_pred.action_type) if base_node else self.f_pred.action_type
            f_prime_content = getattr(base_node, "action_template", {}).get("payload", self.f_pred.content) if base_node else self.f_pred.content
            base_conf = getattr(base_node, "confidence", self.f_pred.confidence) if base_node else self.f_pred.confidence

            if obs_status == CaseStatus.REJECTED:
                confidence_prime = max(0.1, base_conf * 0.4)
                expected_outcome_prime = "escalate"
            elif obs_status == CaseStatus.FAILURE:
                confidence_prime = max(0.1, base_conf * 0.6)
                expected_outcome_prime = "need_input"
            else:
                confidence_prime = base_conf
                expected_outcome_prime = "resolve"

            matched_nid = self.f_pred.matched_node_id
            cost_tier_prime = self.f_pred.cost_tier
            domain_prime = self.f_pred.domain

        # 3. 総合不整合 E = Δ(F, F') の純粋算出 (T0 SPEC 4, 6.1 / C4)
        # 外界帰結ステータスによる直接加算 (delta_reaction) を完全撤廃し、
        # 事前予測 F と同一更新前 M_B から導出された後続作用解釈 F' の純粋な差異として計算する。
        delta_pred = 0.0
        diff_reasons = []

        # (a) ノード境界変異（後続作用によってマッチしたルールが変わったか）
        if matched_nid != self.f_pred.matched_node_id:
            delta_pred += 0.5
            diff_reasons.append(f"ノード境界変異({self.f_pred.matched_node_id} -> {matched_nid})")

        # (b) アクションタイプ変異
        if f_prime_action != self.f_pred.action_type:
            delta_pred += 0.4
            diff_reasons.append(f"アクション変異({self.f_pred.action_type} -> {f_prime_action})")

        # (c) 期待帰結変異
        if expected_outcome_prime != self.f_pred.expected_outcome:
            delta_pred += 0.4
            diff_reasons.append(f"期待帰結変異({self.f_pred.expected_outcome} -> {expected_outcome_prime})")

        # (d) 確信度乖離
        conf_gap = abs(self.f_pred.confidence - confidence_prime)
        if conf_gap > 1e-4:
            delta_pred += 0.4 * conf_gap
            diff_reasons.append(f"確信度乖離(Δ={conf_gap:.3f})")

        # (e) 回答内容の乖離
        if f_prime_content != self.f_pred.content:
            delta_pred += 0.2
            diff_reasons.append("回答内容差異")

        e_pred = delta_pred
        explanation = f"F' 事後解釈: Δ(F,F')={e_pred:.3f}" + (f" [{', '.join(diff_reasons)}]" if diff_reasons else " [解釈完全一致]")

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
            actual_status=obs_status,    # 後方互換性
            actual_outcome=obs_outcome,  # 後方互換性
            e_prediction_delta=round(e_pred, 4), # 後方互換性
            e_input_delta=round(e_input, 4),      # 後方互換性
            explanation=explanation,
        )

        self.e_prediction = round(e_pred, 4)
        self.e_input = round(e_input, 4)
        return self.e_prediction, self.e_input

    def mark_unknown(self) -> Tuple[float, float]:
        """タイムアウト等の理由で結果が回収不能になった場合 (F' は UNKNOWN として解釈)"""
        self.status = CaseStatus.UNKNOWN
        self.resolved_at = datetime.utcnow().isoformat()
        self.outcome_observation = OutcomeObservation(
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
            actual_status=CaseStatus.UNKNOWN,
            actual_outcome="unresolved",
            e_prediction_delta=e_pred,
            e_input_delta=e_input,
            explanation="タイムアウトにより事後結果回収不能：未回収関係 ξ として不確実性熱を残存",
        )
        self.e_prediction = e_pred
        self.e_input = e_input
        return self.e_prediction, self.e_input
