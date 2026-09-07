from dataclasses import dataclass, field
import re
from typing import Optional, List, Dict, Any, Tuple
from .mb_graph import MBGraph, MBNode
from .snapshot import BusinessInput, InterpretationPrediction

@dataclass
class CascadeConfig:
    """推論カスケードの設定（同一解釈条件の保証）"""
    level2_threshold: float = 0.35
    cost_tier0_confidence_boost: float = 0.1
    llm_default_confidence: float = 0.5
    level2_max_confidence: float = 0.85

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
    ):
        self.mb_graph = mb_graph
        self.llm_bridge = llm_bridge
        self.config = config or CascadeConfig()
        # Level 0 キャッシュ: (domain, norm_query) -> node_id
        self.level0_cache: Dict[Tuple[str, str], str] = dict(initial_cache) if initial_cache else {}

    def export_cache(self) -> Dict[Tuple[str, str], str]:
        """現在保持している Level 0 キャッシュの不変スナップショットを複製出力"""
        return dict(self.level0_cache)

    def import_cache(self, cache: Dict[Tuple[str, str], str]):
        """外部キャッシュスナップショットを取り込み"""
        self.level0_cache.update(cache)

    def _normalize(self, text: str) -> str:
        return re.sub(r"\s+", "", text.lower())

    def interpret(self, efp: BusinessInput) -> InterpretationPrediction:
        norm_query = self._normalize(efp.query_text)
        target_domain = efp.category or "any"

        # 有限境界 B による推論空間の拘束:
        # 明示的なワイルドカード (None, "*", "__any__", "any") のみ全域走査を許容し、
        # "general" を含む通常ドメインは厳格に一致するノードのみを候補とする
        def is_domain_eligible(node_domain: str, category: Optional[str]) -> bool:
            if not category or category in ("*", "__any__", "any"):
                return True
            return node_domain == category

        eligible_nodes = [
            node for node in self.mb_graph.list_nodes()
            if is_domain_eligible(node.domain, efp.category)
        ]

        # -------------------------------------------------------------
        # Level 0: 完全一致キャッシュ (Cost Tier 0: ローカル最小コスト)
        # ドメイン境界 B とクエリのタプルで管理
        # -------------------------------------------------------------
        cache_key = (target_domain, norm_query)
        if cache_key in self.level0_cache:
            nid = self.level0_cache[cache_key]
            node = self.mb_graph.get(nid)
            if node and is_domain_eligible(node.domain, efp.category):
                return InterpretationPrediction(
                    action_type=node.action_template.get("type", "direct_reply"),
                    content=node.action_template.get("payload", ""),
                    confidence=min(1.0, node.confidence + self.config.cost_tier0_confidence_boost),
                    matched_node_id=node.id,
                    cost_tier=0,
                    domain=node.domain,
                    expected_outcome="resolve",
                )

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
                    return InterpretationPrediction(
                        action_type=node.action_template.get("type", "direct_reply"),
                        content=node.action_template.get("payload", ""),
                        confidence=node.confidence,
                        matched_node_id=node.id,
                        cost_tier=1,
                        domain=node.domain,
                        expected_outcome="resolve",
                    )

            # 正規表現ルールのチェック
            rule_expr = pattern.get("rule_expr")
            if rule_expr and re.search(rule_expr, efp.query_text, re.IGNORECASE):
                return InterpretationPrediction(
                    action_type=node.action_template.get("type", "direct_reply"),
                    content=node.action_template.get("payload", ""),
                    confidence=node.confidence,
                    matched_node_id=node.id,
                    cost_tier=1,
                    domain=node.domain,
                    expected_outcome="resolve",
                )

        # -------------------------------------------------------------
        # Level 2: 局所類似度マッチング (Cost Tier 2)
        # 日本語対応 文字bi-gram Jaccard類似度 (境界内 eligible_nodes のみ)
        # -------------------------------------------------------------
        def get_bigrams(text: str) -> set:
            cleaned = self._normalize(text)
            if len(cleaned) < 2:
                return {cleaned}
            return {cleaned[i:i+2] for i in range(len(cleaned) - 1)}

        best_node = None
        best_score = 0.0
        query_bigrams = get_bigrams(efp.query_text)

        for node in eligible_nodes:
            for k in node.trigger_pattern.get("exact_keys", []):
                key_bigrams = get_bigrams(k)
                if query_bigrams and key_bigrams:
                    overlap = len(query_bigrams & key_bigrams) / len(query_bigrams | key_bigrams)
                    if overlap > best_score:
                        best_score = overlap
                        best_node = node

        if best_node and best_score >= self.config.level2_threshold:
            return InterpretationPrediction(
                action_type=best_node.action_template.get("type", "direct_reply"),
                content=best_node.action_template.get("payload", ""),
                confidence=min(self.config.level2_max_confidence, best_node.confidence * (0.6 + best_score)),
                matched_node_id=best_node.id,
                cost_tier=2,
                domain=best_node.domain,
                expected_outcome="resolve",
            )

        # -------------------------------------------------------------
        # Level 3: 外部LLM推論器 (Cost Tier 3: 外部高コスト推論)
        # -------------------------------------------------------------
        if self.llm_bridge:
            # 外部LLMまたはモックLLMを呼び出し
            llm_res = self.llm_bridge.resolve(efp)
            return InterpretationPrediction(
                action_type=llm_res.get("type", "direct_reply"),
                content=llm_res.get("payload", "LLMによる汎用回答"),
                confidence=self.config.llm_default_confidence,  # 未知初見のため標準確信度
                matched_node_id=None,
                cost_tier=3,
                domain=efp.category or "unknown",
                expected_outcome="need_input",
            )

        # LLM未設定のデフォルトフォールバック（人間に聞く）
        return InterpretationPrediction(
            action_type="ask_human",
            content="過去事例・ルールが見つかりません。先輩社員へ確認が必要です。",
            confidence=0.1,
            matched_node_id=None,
            cost_tier=3,
            domain=efp.category or "unknown",
            expected_outcome="escalate",
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
