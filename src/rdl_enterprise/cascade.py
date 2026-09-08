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
        interpretation_context_hash: Optional[str] = None, # 完全凍結コンテキストハッシュ (BASE v2.0 §4.2)
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
        # 全解釈条件の暗号論的ハッシュ（未指定の場合はグラフハッシュとunfrozenスコープにフォールバック）
        self.interpretation_context_hash = interpretation_context_hash
        self.context_scope = "frozen" if interpretation_context_hash else "unfrozen"

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
        """
        from datetime import datetime, timezone
        eval_time = self.constraint_evaluation_time or datetime.now(timezone.utc)
        locator_cfg = self.constraint_locator.config
        ctx = ConstraintContext(
            efp=efp,
            current_time=eval_time,
            mb_version=getattr(self.mb_graph, "version", "prod"),
            active_domain=efp.category,
            config=locator_cfg,
        )
        bundle = self.constraint_locator.locate_bundle_for_node(self.mb_graph, node, ctx)
        if bundle is None:
            return 0.0
        probe = RuptureProbe(locator_cfg)
        result = probe.probe(bundle, self.mb_graph, ctx)
        if result.verdict == "survive":
            cap = locator_cfg.constraint_boost_cap
            core_score = getattr(bundle, "core_constraint_score", bundle.constraint_score)
            return min(cap, core_score * cap)
        return 0.0

    def select_active_constraint_subgraph(
        self,
        eligible_nodes: List[MBNode],
        efp: BusinessInput,
        limit: int = 4,
        relevance_floor: float = 0.05,
    ) -> List[MBNode]:
        r"""
        活性化拘束サブグラフ選出器 (ContextSelector: BASE v2.0 §4.2)。
        有限境界 B 内の利用可能関係 (eligible_nodes) の中から、
        入力 EFP との適合度・トリガー共起・相互関係性に基づいて
        実際に F 形成候補となり得る活性化サブグラフ L_candidate を選出する。

        【関係活性化伝播モデル (Graph Relation Propagation)】:
        1. 入力クエリに対して適合度が閾値 (relevance_floor) 以上のシードノード群 (Seed Nodes) を特定。
        2. シードノードから MBGraph 上の明示的関係エッジ (support, authority 等) を
           1-hop 伝播 (Relation Edge Propagation) させて接続ノード群を展開。
        3. 関連性ゼロ (rel == 0) の孤立ノードは、承認実績が高くても原則として除外。
        """
        if not eligible_nodes:
            return []

        from .constraint import _compute_relevance, _compute_authority_weight

        # 1. 各ノードの直接関連度 (Relevance) とスコアを算出
        node_map = {n.id: n for n in eligible_nodes}
        rel_map: Dict[str, float] = {}
        scored_nodes: List[Tuple[float, MBNode]] = []

        for node in eligible_nodes:
            rel = _compute_relevance(efp.query_text, node.trigger_pattern)
            rel_map[node.id] = rel
            src_str = node.approval_count / (node.approval_count + node.rejection_count + 1.0)
            auth_w = _compute_authority_weight(node.authority_level)
            # 適合度を主軸とし、ソース拘束・権威で補正
            score = rel * 0.7 + src_str * 0.2 + auth_w * 0.1
            scored_nodes.append((score, node))

        # 2. 直接適合シードノード (Seed Nodes: rel >= relevance_floor) の抽出
        seed_nodes = [n for score, n in scored_nodes if rel_map[n.id] >= relevance_floor]

        # 3. グラフ関係伝播 (Directed & Weighted 1-hop Relation Propagation: BASE v2.0 §4.2)
        # シードノードから明示的に support, authority 等で接続しているノードを展開
        activated_ids = set(n.id for n in seed_nodes)
        edge_weight_map: Dict[str, float] = {n.id: 1.0 for n in seed_nodes}
        for sn in list(seed_nodes):
            # (a) 明示的 node_relations の伝播
            for target_id, rel_type in getattr(sn, "node_relations", {}).items():
                if target_id in node_map:
                    if rel_type in ("support", "authority", "policy_authority"):
                        activated_ids.add(target_id)
                        edge_weight_map[target_id] = max(edge_weight_map.get(target_id, 0.0), 0.8)
                    elif rel_type in ("inferred_support", "co_occurs"):
                        if rel_map.get(target_id, 0.0) >= (relevance_floor * 0.5):
                            activated_ids.add(target_id)
                            edge_weight_map[target_id] = max(edge_weight_map.get(target_id, 0.0), 0.4)
            # (b) トリガー共起 (find_co_occurring_nodes) の伝播
            if hasattr(self.mb_graph, "find_co_occurring_nodes"):
                co_nodes = self.mb_graph.find_co_occurring_nodes(sn, limit=3)
                for cn in co_nodes:
                    if cn.id in node_map and rel_map.get(cn.id, 0.0) >= (relevance_floor * 0.5):
                        activated_ids.add(cn.id)
                        edge_weight_map[cn.id] = max(edge_weight_map.get(cn.id, 0.0), 0.5)

        # 4. 活性化サブグラフの候補選別
        if activated_ids:
            candidates = [n for n in eligible_nodes if n.id in activated_ids]
            # スコア降順に並べ替え (伝播重みも加味)
            candidates.sort(
                key=lambda n: (
                    rel_map[n.id] * 0.7 +
                    (n.approval_count / (n.approval_count + n.rejection_count + 1.0)) * 0.2 +
                    _compute_authority_weight(n.authority_level) * 0.1 +
                    edge_weight_map.get(n.id, 0.0) * 0.1
                ),
                reverse=True,
            )
            return candidates[:limit]

        # 【P0: 関連性皆無の完全未知クエリにおける無関係権威ノード注入の厳格禁止】
        # 入力と関係する seed が存在しない場合、権威や承認数だけで無関係ノードをでっち上げず
        # 空リストを返却して未回収関係 (ξ) として保持する (authority は relevance の代替ではない)。
        return []

    def _finalize_prediction(
        self,
        pred: InterpretationPrediction,
        efp: BusinessInput,
        conditions_hash: str = "",
        view_hash: str = "",
        provider_request_id: Optional[str] = None,
        provider_response_id: Optional[str] = None,
    ) -> InterpretationPrediction:
        r"""
        全推論層 (L0〜L3, Fallback) 共通の予測確定パイプライン (BASE v2.0 §4.2)。
        出口を一本化し、完全凍結された解釈条件ハッシュ (context_hash) と
        外生条件集合 K (conditions_hash)、および知識境界ビュー (view_hash) を
        包括した InterpretationTrace を確実に生成・刻印する。
        """
        from .snapshot import InterpretationTrace
        ctx_hash = self.interpretation_context_hash or getattr(self.mb_graph, "content_hash", lambda: "unknown")()
        trace = InterpretationTrace.create(
            context_hash=ctx_hash,
            conditions_hash=conditions_hash,
            pred=pred,
            mb_view_hash=view_hash,
            selected_locus_ids=pred.selected_locus_ids,
            applied_locus_ids=pred.applied_locus_ids,
            context_scope=self.context_scope,
            provider_request_id=provider_request_id,
            provider_response_id=provider_response_id,
        )
        pred.metadata["interpretation_trace"] = trace
        return pred

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

        def is_domain_eligible(node_domain: str, category: Optional[str]) -> bool:
            if not category or category in ("*", "__any__", "any"):
                return True
            return node_domain == category

        eligible_nodes = [
            node for node in self.mb_graph.list_nodes()
            if is_domain_eligible(node.domain, efp.category) and (node.id not in exclude_set)
        ]
        available_locus_ids = [n.id for n in eligible_nodes]

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
            pred = InterpretationPrediction(
                action_type=node.action_template.get("type", "direct_reply"),
                content=node.action_template.get("payload", ""),
                confidence=min(1.0, conf),
                matched_node_id=node.id,
                cost_tier=cost_tier,
                domain=node.domain,
                expected_outcome=outcome,
                available_locus_ids=available_locus_ids,
                selected_locus_ids=[node.id],
                applied_locus_ids=[node.id],
                constraint_locus_ids=[node.id],
                locus_basis="direct_match",
            )
            return self._finalize_prediction(pred, efp)

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
            for key in pattern.get("exact_keys", []):
                if self._normalize(key) == norm_query or key.lower() in efp.query_text.lower():
                    self.level0_cache[cache_key] = node.id
                    boost = 0.0 if skip_constraint_boost else self._constraint_boost(node, efp)
                    base_c = node.confidence + boost
                    return _build_prediction(node, base_c, cost_tier=1)

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
            from rdl_enterprise.snapshot import CounterfactualInput, CounterfactualMBView, BridgeExecutionTrace

            # 活性化拘束サブグラフ L_candidate の選出 (ContextSelector: BASE v2.0 §4.2)
            active_subgraph = self.select_active_constraint_subgraph(eligible_nodes, efp, limit=4)
            selected_locus_ids = [n.id for n in active_subgraph]
            mb_hash = getattr(self.mb_graph, "content_hash", lambda: "unknown")()

            if replay_token is not None:
                # 反実仮想再演 (Counterfactual Replay): 外生固定条件集合 K の下での再演
                mb_view = CounterfactualMBView.from_nodes(
                    available_nodes=active_subgraph,
                    excluded_node_ids=list(exclude_set),
                    mb_content_hash=mb_hash,
                    domain=target_domain,
                )
                cf_input = CounterfactualInput(
                    efp=efp,
                    replay_token=replay_token,
                    mb_view=mb_view,
                    excluded_node_ids=list(exclude_set),
                    available_nodes=active_subgraph,
                    domain=target_domain,
                )
                cf_applied = False
                exec_trace: Optional[BridgeExecutionTrace] = None

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

                # Bridge からの BridgeExecutionTrace 抽出または適用ビュー照合 (BASE v2.0 §4.2)
                # 【自己捏造の厳格禁止 (Self-Exception Prohibition)】:
                # Bridge が applied_mb_view_hash または execution_trace を実証的に返さなかった場合、
                # requested_mb_view_hash から applied を自動生成することを禁止する。
                if isinstance(llm_res, dict):
                    if "execution_trace" in llm_res and isinstance(llm_res["execution_trace"], BridgeExecutionTrace):
                        exec_trace = llm_res["execution_trace"]
                    elif "applied_mb_view_hash" in llm_res and llm_res["applied_mb_view_hash"]:
                        applied_ids = tuple(llm_res.get("applied_node_ids", selected_locus_ids))
                        exec_trace = BridgeExecutionTrace(
                            requested_mb_view_hash=mb_view.view_hash,
                            applied_mb_view_hash=str(llm_res["applied_mb_view_hash"]),
                            conditions_hash=getattr(replay_token, "conditions_hash", ""),
                            applied_node_ids=applied_ids,
                        )
                elif hasattr(self.llm_bridge, "applied_mb_view_hash") and getattr(self.llm_bridge, "applied_mb_view_hash", None):
                    # Bridge インスタンスが客観的証憑として applied_mb_view_hash プロパティを開示している場合
                    exec_trace = BridgeExecutionTrace(
                        requested_mb_view_hash=mb_view.view_hash,
                        applied_mb_view_hash=str(self.llm_bridge.applied_mb_view_hash),
                        conditions_hash=getattr(replay_token, "conditions_hash", ""),
                        applied_node_ids=tuple(selected_locus_ids),
                    )

                pred_metadata["counterfactual_verified"] = cf_applied
                pred_metadata["mb_view_hash"] = mb_view.view_hash
                pred_metadata["conditions_hash"] = getattr(replay_token, "conditions_hash", "")
                if exec_trace is not None:
                    pred_metadata["execution_trace"] = exec_trace
            else:
                # 通常推論 (Normal Resolve):
                # 活性化拘束サブグラフ L_candidate を bridge に提示して推論
                mb_view = CounterfactualMBView.from_nodes(
                    available_nodes=active_subgraph,
                    excluded_node_ids=[],
                    mb_content_hash=mb_hash,
                    domain=target_domain,
                )
                exec_trace: Optional[BridgeExecutionTrace] = None
                if hasattr(self.llm_bridge, "resolve_with_context") and callable(self.llm_bridge.resolve_with_context):
                    llm_res = self.llm_bridge.resolve_with_context(efp, mb_view)
                    if isinstance(llm_res, tuple) and len(llm_res) == 2:
                        llm_res, actual_token = llm_res
                elif hasattr(self.llm_bridge, "resolve_with_trace") and callable(self.llm_bridge.resolve_with_trace):
                    llm_res, actual_token = self.llm_bridge.resolve_with_trace(efp)
                else:
                    try:
                        llm_res = self.llm_bridge.resolve(efp, mb_view=mb_view)
                    except TypeError:
                        llm_res = self.llm_bridge.resolve(efp)

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

                # Bridge からの BridgeExecutionTrace 抽出または適用ビュー照合 (BASE v2.0 §4.2)
                if isinstance(llm_res, dict):
                    if "execution_trace" in llm_res and isinstance(llm_res["execution_trace"], BridgeExecutionTrace):
                        exec_trace = llm_res["execution_trace"]
                    elif "applied_mb_view_hash" in llm_res and llm_res["applied_mb_view_hash"]:
                        applied_ids = tuple(llm_res.get("applied_node_ids", selected_locus_ids))
                        exec_trace = BridgeExecutionTrace(
                            requested_mb_view_hash=mb_view.view_hash,
                            applied_mb_view_hash=str(llm_res["applied_mb_view_hash"]),
                            conditions_hash=getattr(actual_token, "conditions_hash", "") if actual_token else "",
                            applied_node_ids=applied_ids,
                        )
                elif hasattr(self.llm_bridge, "applied_mb_view_hash") and getattr(self.llm_bridge, "applied_mb_view_hash", None):
                    exec_trace = BridgeExecutionTrace(
                        requested_mb_view_hash=mb_view.view_hash,
                        applied_mb_view_hash=str(self.llm_bridge.applied_mb_view_hash),
                        conditions_hash=getattr(actual_token, "conditions_hash", "") if actual_token else "",
                        applied_node_ids=tuple(selected_locus_ids),
                    )

                pred_metadata["mb_view_hash"] = mb_view.view_hash
                pred_metadata["conditions_hash"] = getattr(actual_token, "conditions_hash", "") if actual_token else ""
                if exec_trace is not None:
                    pred_metadata["execution_trace"] = exec_trace

            # 実際に注入・適用されたノード群 (applied_locus_ids) の特定
            applied_locus_ids = list(selected_locus_ids)
            if not selected_locus_ids:
                locus_basis = "unresolved_selection"
            else:
                locus_basis = "context_selected"

            if isinstance(llm_res, dict):
                if "applied_node_ids" in llm_res and llm_res["applied_node_ids"]:
                    applied_locus_ids = list(llm_res["applied_node_ids"])
                    locus_basis = "bridge_applied"
                elif "used_node_ids" in llm_res and llm_res["used_node_ids"]:
                    applied_locus_ids = list(llm_res["used_node_ids"])
                    locus_basis = "bridge_applied"

            base_conf = self.config.llm_default_confidence
            outcome = "need_input"
            if is_prime:
                if human_rejected:
                    outcome = "escalate"
                    base_conf = max(0.05, base_conf * 0.4)
                elif not user_resolved:
                    outcome = "need_input"
                    base_conf = max(0.05, base_conf * 0.6)

            pred = InterpretationPrediction(
                action_type=llm_res.get("type", "direct_reply") if isinstance(llm_res, dict) else "direct_reply",
                content=llm_res.get("payload", "LLMによる汎用回答") if isinstance(llm_res, dict) else str(llm_res),
                confidence=base_conf,
                matched_node_id=None,
                cost_tier=3,
                domain=efp.category or "unknown",
                expected_outcome=outcome,
                replay_token=actual_token,
                available_locus_ids=available_locus_ids,
                selected_locus_ids=selected_locus_ids,
                applied_locus_ids=applied_locus_ids,
                constraint_locus_ids=applied_locus_ids,
                locus_basis=locus_basis,
                metadata=pred_metadata,
            )
            return self._finalize_prediction(
                pred,
                efp,
                conditions_hash=pred_metadata.get("conditions_hash", ""),
                view_hash=pred_metadata.get("mb_view_hash", ""),
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

        fallback_pred = InterpretationPrediction(
            action_type="ask_human",
            content="過去事例・ルールが見つかりません。先輩社員へ確認が必要です。",
            confidence=fallback_conf,
            matched_node_id=None,
            cost_tier=3,
            domain=efp.category or "unknown",
            expected_outcome=fallback_outcome,
            available_locus_ids=available_locus_ids,
            selected_locus_ids=[],
            applied_locus_ids=[],
            constraint_locus_ids=[],
            locus_basis="fallback",
        )
        return self._finalize_prediction(fallback_pred, efp)

    def sediment_level0(self, domain: str, query_text: str, node_id: str):
        """
        成功確認後に確定した判断を Level 0 キャッシュへ沈澱 (BASE v2.0 代謝閉ループ)
        """
        norm_q = self._normalize(query_text)
        self.level0_cache[(domain or "general", norm_q)] = node_id

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
        self.sediment_level0(category, efp.query_text, new_id)
        return new_node
