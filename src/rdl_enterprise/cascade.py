from dataclasses import dataclass, field
import re
from typing import Optional, List, Dict, Any, Tuple
from .mb_graph import MBGraph, MBNode
from .snapshot import BusinessInput, InterpretationPrediction
from .constraint import (
    ConstraintConfig, ConstraintContext, RelationConstraintLocator, RuptureProbe,
)

@dataclass
class CascadeConfig:
    """推論カスケードの設定（同一解釈条件の保証）"""
    level2_threshold: float = 0.35
    cost_tier0_confidence_boost: float = 0.1
    llm_default_confidence: float = 0.5
    level2_max_confidence: float = 0.85
    # ※ constraint_boost_cap は ConstraintConfig で一元管理（ここには持たない）

class InterpCascade:
    """
    多層推論カスケード (Level 0 〜 Level 3)
    経験によって判断が高コスト層から低コスト層へと沈澱する
    """
    def __init__(
        self,
        mb_graph: MBGraph,
        llm_bridge: Optional[Any] = None,
        config: Optional[CascadeConfig] = None,
        initial_cache: Optional[Dict[Tuple[str, str], str]] = None,
        constraint_config: Optional[ConstraintConfig] = None,
        constraint_evaluation_time: Optional[Any] = None,  # datetime（FrozenInterpretationContext から伝播）
    ):
        self.mb_graph = mb_graph
        self.llm_bridge = llm_bridge
        self.config = config or CascadeConfig()
        # Level 0 キャッシュ: (domain, norm_query) -> node_id
        self.level0_cache: Dict[Tuple[str, str], str] = dict(initial_cache) if initial_cache else {}
        # 関係拘束評価器（カスタム ConstraintConfig を保持）
        self.constraint_locator = RelationConstraintLocator(constraint_config or ConstraintConfig())
        # 凍結された関係拘束評価時刻（None の場合は _constraint_boost() が now() にフォールバック）
        self.constraint_evaluation_time = constraint_evaluation_time

    def export_cache(self) -> Dict[Tuple[str, str], str]:
        """現在保持している Level 0 キャッシュの不変スナップショットを複製出力"""
        return dict(self.level0_cache)

    def import_cache(self, cache: Dict[Tuple[str, str], str]):
        """外部キャッシュスナップショットを取り込み"""
        self.level0_cache.update(cache)

    def _normalize(self, text: str) -> str:
        return re.sub(r"\s+", "", text.lower())

    def _constraint_boost(self, node: MBNode, efp: BusinessInput) -> float:
        """
        現在の問い EFP に対するノードの関係拘束スコアを算出し、
        confidence への寄与分（boost）を返す。
        survive した拘束のみ boost、break / unresolved は 0。

        【修正点】
          - 評価時刻: FrozenInterpretationContext から伝播された constraint_evaluation_time を使用。
            None の場合のみ datetime.now() にフォールバック（凍結なし cascade での使用時）。
          - 設定: self.constraint_locator.config を ConstraintContext に渡す（カスタム設定が反映される）。
          - boost cap: ConstraintConfig.constraint_boost_cap を参照（CascadeConfig の二重定義を解消）。
        """
        from datetime import datetime, timezone
        # 凍結評価時刻を優先（F と F' で同じ freshness になることを保証）
        eval_time = self.constraint_evaluation_time or datetime.now(timezone.utc)
        locator_cfg = self.constraint_locator.config
        ctx = ConstraintContext(
            efp=efp,
            current_time=eval_time,
            mb_version=getattr(self.mb_graph, "version", "prod"),
            active_domain=efp.category,
            config=locator_cfg,  # カスタム ConstraintConfig を必ず渡す
        )
        bundle = self.constraint_locator.locate_bundle_for_node(self.mb_graph, node, ctx)
        if bundle is None:
            return 0.0
        probe = RuptureProbe(locator_cfg)
        result = probe.probe(bundle, self.mb_graph, ctx)
        if result.verdict == "survive":
            cap = locator_cfg.constraint_boost_cap  # ConstraintConfig から読む（二重定義解消）
            core_score = getattr(bundle, "core_constraint_score", bundle.constraint_score)
            return min(cap, core_score * cap)
        return 0.0


    def interpret(
        self,
        efp: BusinessInput,
        exclude_node_ids: Optional[Any] = None,
        skip_constraint_boost: bool = False,
        replay_token: Optional[Any] = None,
    ) -> InterpretationPrediction:
        r"""
        事前予測 F または事後解釈 F' の形成。
        exclude_node_ids: 破断検査（RuptureProbe）における実効的除去摂動用。
                          指定されたノード群を仮想的に排除した M_B \ bundle の解釈を形成する。
        skip_constraint_boost: RuptureProbe 内での再帰呼び出しを防止するためのフラグ。
        replay_token: 反実仮想実験における外生固定条件集合 K (ReplayToken)。
                      指定された場合のみ反実仮想再演パスを実行する。
        """
        norm_query = self._normalize(efp.query_text)
        target_domain = efp.category or "any"
        exclude_set = set(exclude_node_ids) if exclude_node_ids else set()

        # 有限境界 B による推論空間の拘束:
        # 明示的なワイルドカード (None, "*", "__any__", "any") のみ全域走査を許容し、
        # "general" を含む通常ドメインは厳格に一致するノードのみを候補とする
        def is_domain_eligible(node_domain: str, category: Optional[str]) -> bool:
            if not category or category in ("*", "__any__", "any"):
                return True
            return node_domain == category

        eligible_nodes = [
            node for node in self.mb_graph.list_nodes()
            if is_domain_eligible(node.domain, efp.category) and (node.id not in exclude_set)
        ]

        is_prime = bool(efp.metadata.get("is_efp_prime", False)) if hasattr(efp, "metadata") and efp.metadata else getattr(efp, "is_prime", False)
        metadata = getattr(efp, "metadata", {}) or {}
        user_resolved = metadata.get("user_resolved", True)
        human_rejected = metadata.get("human_rejected", False)

        def _build_prediction(node: MBNode, base_conf: float, cost_tier: int) -> InterpretationPrediction:
            outcome = "resolve"
            conf = base_conf
            if is_prime:
                if human_rejected:
                    outcome = "escalate"
                    conf = max(0.1, base_conf * 0.4)
                elif not user_resolved:
                    outcome = "need_input"
                    conf = max(0.1, base_conf * 0.6)
            return InterpretationPrediction(
                action_type=node.action_template.get("type", "direct_reply"),
                content=node.action_template.get("payload", ""),
                confidence=min(1.0, conf),
                matched_node_id=node.id,
                cost_tier=cost_tier,
                domain=node.domain,
                expected_outcome=outcome,
            )

        # -------------------------------------------------------------
        # Level 0: 完全一致キャッシュ (Cost Tier 0: ローカル最小コスト)
        # ドメイン境界 B とクエリのタプルで管理
        # -------------------------------------------------------------
        cache_key = (target_domain, norm_query)
        if cache_key in self.level0_cache:
            nid = self.level0_cache[cache_key]
            if nid not in exclude_set:
                node = self.mb_graph.get(nid)
                if node and is_domain_eligible(node.domain, efp.category):
                    boost = 0.0 if skip_constraint_boost else self._constraint_boost(node, efp)
                    base_c = node.confidence + self.config.cost_tier0_confidence_boost + boost
                    return _build_prediction(node, base_c, cost_tier=0)

        # -------------------------------------------------------------
        # Level 1: 構造化確定ルール・正規表現 (Cost Tier 1)
        # 境界内 (eligible_nodes) のみを探索
        # -------------------------------------------------------------
        for node in eligible_nodes:
            pattern = node.trigger_pattern
            # 完全一致キー群のチェック
            for key in pattern.get("exact_keys", []):
                if self._normalize(key) == norm_query or key.lower() in efp.query_text.lower():
                    # ヒットしたらLevel 0キャッシュに昇格 (ドメイン境界付き)
                    self.level0_cache[cache_key] = node.id
                    boost = 0.0 if skip_constraint_boost else self._constraint_boost(node, efp)
                    base_c = node.confidence + boost
                    return _build_prediction(node, base_c, cost_tier=1)

            # ルール式 (正規表現等) の評価
            rule_expr = pattern.get("rule_expr")
            if rule_expr:
                try:
                    if re.search(rule_expr, efp.query_text, re.IGNORECASE):
                        self.level0_cache[cache_key] = node.id
                        boost = 0.0 if skip_constraint_boost else self._constraint_boost(node, efp)
                        base_c = node.confidence + boost
                        return _build_prediction(node, base_c, cost_tier=1)
                except re.error:
                    pass

        # -------------------------------------------------------------
        # Level 2: 局所推論器・類似度検索 (Cost Tier 2)
        # bi-gram Jaccard による簡易類似度
        # -------------------------------------------------------------
        best_node = None
        best_score = 0.0
        query_bigrams = set(norm_query[i:i+2] for i in range(len(norm_query) - 1)) if len(norm_query) >= 2 else {norm_query}

        def get_bigrams(s: str):
            ns = self._normalize(s)
            return set(ns[i:i+2] for i in range(len(ns) - 1)) if len(ns) >= 2 else {ns}

        for node in eligible_nodes:
            for k in node.trigger_pattern.get("exact_keys", []):
                key_bigrams = get_bigrams(k)
                if query_bigrams and key_bigrams:
                    overlap = len(query_bigrams & key_bigrams) / len(query_bigrams | key_bigrams)
                    if overlap > best_score:
                        best_score = overlap
                        best_node = node

        if best_node and best_score >= self.config.level2_threshold:
            boost = (
                0.0
                if skip_constraint_boost
                else self._constraint_boost(best_node, efp)
            )
            base_c = min(self.config.level2_max_confidence,
                         best_node.confidence * (0.6 + best_score) + boost)
            return _build_prediction(best_node, base_c, cost_tier=2)

        # -------------------------------------------------------------
        # Level 3: 外部LLM推論器 (Cost Tier 3: 外部高コスト推論)
        # -------------------------------------------------------------
        if self.llm_bridge:
            actual_token = replay_token
            pred_metadata: Dict[str, Any] = {}
            if replay_token is not None:
                # 反実仮想再演 (Counterfactual Replay): 外生固定条件集合 K の下での再演
                # BASE v2.0: 唯一の介入変数 (M_B \ bundle の有無) を CounterfactualMBView & CounterfactualInput として明示伝達
                from rdl_enterprise.snapshot import CounterfactualInput, CounterfactualMBView
                mb_hash = getattr(self.mb_graph, "content_hash", lambda: "unknown")()
                mb_view = CounterfactualMBView.from_nodes(
                    available_nodes=eligible_nodes,
                    excluded_node_ids=list(exclude_set),
                    mb_content_hash=mb_hash,
                    domain=target_domain,
                )
                cf_input = CounterfactualInput(
                    efp=efp,
                    replay_token=replay_token,
                    mb_view=mb_view,
                    excluded_node_ids=list(exclude_set),
                    available_nodes=eligible_nodes,
                    domain=target_domain,
                )
                cf_applied = False
                if hasattr(self.llm_bridge, "resolve_counterfactual") and callable(self.llm_bridge.resolve_counterfactual):
                    try:
                        llm_res = self.llm_bridge.resolve_counterfactual(efp, replay_token, counterfactual_input=cf_input)
                        cf_applied = True
                    except TypeError:
                        try:
                            llm_res = self.llm_bridge.resolve_counterfactual(efp, replay_token)
                            cf_applied = False
                        except TypeError:
                            try:
                                llm_res = self.llm_bridge.resolve_counterfactual(cf_input)
                                cf_applied = True
                            except TypeError:
                                llm_res = self.llm_bridge.resolve_counterfactual(efp)
                                cf_applied = False
                elif hasattr(self.llm_bridge, "resolve_replay") and callable(self.llm_bridge.resolve_replay):
                    try:
                        llm_res = self.llm_bridge.resolve_replay(efp, replay_token, counterfactual_input=cf_input)
                        cf_applied = True
                    except TypeError:
                        try:
                            llm_res = self.llm_bridge.resolve_replay(efp, replay_token)
                            cf_applied = False
                        except TypeError:
                            try:
                                llm_res = self.llm_bridge.resolve_replay(cf_input)
                                cf_applied = True
                            except TypeError:
                                llm_res = self.llm_bridge.resolve_replay(efp)
                                cf_applied = False
                else:
                    llm_res = self.llm_bridge.resolve(efp)
                    cf_applied = False

                pred_metadata["counterfactual_verified"] = cf_applied
                pred_metadata["mb_view_hash"] = mb_view.view_hash
                pred_metadata["conditions_hash"] = getattr(replay_token, "conditions_hash", "")
            else:
                # 通常推論 (Normal Resolve): 通常の未知案件解釈作用
                if hasattr(self.llm_bridge, "resolve_with_trace") and callable(self.llm_bridge.resolve_with_trace):
                    llm_res, actual_token = self.llm_bridge.resolve_with_trace(efp)
                else:
                    llm_res = self.llm_bridge.resolve(efp)
                    # 1. resolve() 自体から排出された実際の推論証跡 K_actual を最優先採用
                    if isinstance(llm_res, dict) and "replay_token" in llm_res and llm_res["replay_token"] is not None:
                        actual_token = llm_res["replay_token"]
                    elif hasattr(self.llm_bridge, "capture_counterfactual_context") and callable(self.llm_bridge.capture_counterfactual_context):
                        try:
                            actual_token = self.llm_bridge.capture_counterfactual_context(efp)
                        except Exception:
                            actual_token = None
                    elif hasattr(self.llm_bridge, "create_replay_token") and callable(self.llm_bridge.create_replay_token):
                        try:
                            actual_token = self.llm_bridge.create_replay_token()
                        except Exception:
                            actual_token = None

            base_conf = self.config.llm_default_confidence
            outcome = "need_input"
            if is_prime:
                if human_rejected:
                    outcome = "escalate"
                    base_conf = max(0.05, base_conf * 0.4)
                elif not user_resolved:
                    outcome = "need_input"
                    base_conf = max(0.05, base_conf * 0.6)
            return InterpretationPrediction(
                action_type=llm_res.get("type", "direct_reply"),
                content=llm_res.get("payload", "LLMによる汎用回答"),
                confidence=base_conf,  # 未知初見のため標準確信度
                matched_node_id=None,
                cost_tier=3,
                domain=efp.category or "unknown",
                expected_outcome=outcome,
                replay_token=actual_token,
                metadata=pred_metadata,
            )

        # LLM未設定のデフォルトフォールバック（人間に聞く）
        fallback_conf = 0.1
        fallback_outcome = "escalate"
        if is_prime:
            if human_rejected:
                fallback_conf = 0.04
                fallback_outcome = "escalate"
            elif not user_resolved:
                fallback_conf = 0.06
                fallback_outcome = "need_input"

        return InterpretationPrediction(
            action_type="ask_human",
            content="過去事例・ルールが見つかりません。先輩社員へ確認が必要です。",
            confidence=fallback_conf,
            matched_node_id=None,
            cost_tier=3,
            domain=efp.category or "unknown",
            expected_outcome=fallback_outcome,
        )

    def crystallize_rule(self, efp: BusinessInput, resolution_text: str, category: str, approved: bool = True):
        """
        LLMや人間によって解決された案件を、新しい Level 1 / Level 0 ノードとして M_B に定着（沈澱）させる
        """
        new_id = f"node_learned_{len(self.mb_graph.nodes) + 1:03d}"
        new_node = MBNode(
            id=new_id,
            domain=category,
            trigger_pattern={
                "exact_keys": [efp.query_text.strip()],
                "rule_expr": None,
            },
            action_template={
                "type": "direct_reply",
                "payload": resolution_text,
            },
            authority_level="auto",
            confidence=0.7 if approved else 0.5,
            success_count=1,
            approval_count=1 if approved else 0,
        )
        self.mb_graph.add_or_update(new_node)
        # Level 0 キャッシュにも即座に登録（ドメイン境界付き）
        self.level0_cache[(category, self._normalize(efp.query_text))] = new_id
        return new_node
