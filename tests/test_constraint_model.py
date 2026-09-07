"""
test_constraint_model.py ― Relational Constraint モデルのテスト
(BASE v2.0 §4.2 / SPEC v2.0 §6.2 整合)
"""
import unittest
from datetime import datetime, timezone, timedelta

from rdl_enterprise.mb_graph import MBGraph, MBNode
from rdl_enterprise.snapshot import BusinessInput
from rdl_enterprise.constraint import (
    ConstraintConfig,
    ConstraintContext,
    ConstraintBundle,
    RelationConstraintLocator,
    RuptureProbe,
    RuptureResult,
)
from rdl_enterprise.cascade import InterpCascade, CascadeConfig
from rdl_enterprise.h_state import HState


def _make_node(
    node_id: str,
    domain: str = "general",
    exact_keys: list = None,
    authority_level: str = "auto",
    confidence: float = 0.7,
    success_count: int = 0,
    approval_count: int = 0,
    failure_count: int = 0,
    rejection_count: int = 0,
    last_updated: str = None,
) -> MBNode:
    if last_updated is None:
        last_updated = datetime.utcnow().isoformat()
    return MBNode(
        id=node_id,
        domain=domain,
        trigger_pattern={"exact_keys": exact_keys or [], "rule_expr": None},
        action_template={"type": "direct_reply", "payload": f"回答_{node_id}"},
        authority_level=authority_level,
        confidence=confidence,
        success_count=success_count,
        approval_count=approval_count,
        failure_count=failure_count,
        rejection_count=rejection_count,
        last_updated=last_updated,
    )


def _make_efp(query: str, category: str = "general") -> BusinessInput:
    return BusinessInput(
        ticket_id="t_test",
        user_id="u_test",
        category=category,
        query_text=query,
    )


def _make_ctx(efp: BusinessInput, now: datetime = None) -> ConstraintContext:
    return ConstraintContext(
        efp=efp,
        current_time=now or datetime.now(timezone.utc),
        mb_version="v1.0",
        active_domain=efp.category,
    )


class TestRelationConstraintLocator(unittest.TestCase):

    def test_strong_fresh_node_gets_high_constraint_score(self):
        """
        新鮮で高承認のノードは constraint_score が高くなる。
        BASE v2.0: 拘束強度は問い・時点・断面によって相対的に立ち上がる。
        """
        graph = MBGraph()
        node = _make_node(
            "node_A",
            exact_keys=["有給申請の方法を教えてください"],
            authority_level="require_approval",
            confidence=0.8,
            success_count=10,
            approval_count=8,
            failure_count=0,
            rejection_count=0,
        )
        graph.add_or_update(node)

        efp = _make_efp("有給申請の方法を教えてください")
        ctx = _make_ctx(efp)
        locator = RelationConstraintLocator()
        bundles = locator.locate(graph, ctx)

        self.assertTrue(len(bundles) > 0)
        top = bundles[0]
        self.assertIn("node_A", top.node_ids)
        # authority が require_approval かつ fresh なので高スコアのはず
        self.assertGreater(top.constraint_score, 0.5)

    def test_stale_node_gets_low_freshness(self):
        """
        180 日前に更新されたノードは freshness が低くなる。
        同じ inertia（success/approval が多い）でも、時刻断面が違えば拘束スコアが下がる。
        BASE v2.0 §4.2.1: 拘束強度は時点によって変化する。
        """
        old_date = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()
        graph = MBGraph()
        stale_node = _make_node(
            "stale_node",
            exact_keys=["古いルールの確認"],
            success_count=100,
            approval_count=80,
            failure_count=0,
            rejection_count=0,
            last_updated=old_date,
        )
        fresh_node = _make_node(
            "fresh_node",
            exact_keys=["古いルールの確認"],
            success_count=5,
            approval_count=3,
            failure_count=0,
            rejection_count=0,
        )
        graph.add_or_update(stale_node)
        graph.add_or_update(fresh_node)

        efp = _make_efp("古いルールの確認")
        ctx = _make_ctx(efp)
        locator = RelationConstraintLocator()
        bundles = locator.locate(graph, ctx)

        stale_bundle = next((b for b in bundles if "stale_node" in b.node_ids), None)
        fresh_bundle = next((b for b in bundles if "fresh_node" in b.node_ids), None)

        self.assertIsNotNone(stale_bundle)
        self.assertIsNotNone(fresh_bundle)
        # 古いノードの freshness は低い
        self.assertLess(stale_bundle.freshness, 0.5)
        # 新鮮なノードの freshness は高い
        self.assertGreater(fresh_bundle.freshness, 0.8)

    def test_bridge_node_detected(self):
        """
        そのノードを除くと解釈可能なクエリが大幅に減少するノードは bridge として検出される。
        BASE v2.0: 「切れると全体が変わる場所」も拘束位置として扱う。
        """
        graph = MBGraph()
        # bridge_node だけが "専用クエリ" に回答できる
        bridge_node = _make_node(
            "bridge_node",
            exact_keys=["専用クエリ_A", "専用クエリ_B", "専用クエリ_C"],
        )
        # other_node はそれとは別のクエリのみ
        other_node = _make_node(
            "other_node",
            exact_keys=["別クエリ"],
        )
        graph.add_or_update(bridge_node)
        graph.add_or_update(other_node)

        efp = _make_efp("専用クエリ_A")
        ctx = _make_ctx(efp)
        locator = RelationConstraintLocator()
        bundles = locator.locate(graph, ctx)

        bridge_bundle = next(
            (b for b in bundles if "bridge_node" in b.node_ids and b.is_structural_bridge),
            None,
        )
        self.assertIsNotNone(bridge_bundle, "bridge_node は is_structural_bridge=True で検出されるべき")


class TestRuptureProbe(unittest.TestCase):

    def test_rupture_stale_node_breaks(self):
        """
        365 日前更新のノードは freshness が極めて低く（≈0.06）、
        RuptureProbe が break を返す。opposing_strength > 1 となり H 蓄積が増加する。
        """
        old_date = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        graph = MBGraph()
        node = _make_node(
            "stale",
            exact_keys=["古いルール"],
            last_updated=old_date,
        )
        graph.add_or_update(node)

        efp = _make_efp("古いルール")
        ctx = _make_ctx(efp)
        locator = RelationConstraintLocator()
        bundles = locator.locate(graph, ctx)
        bundle = next((b for b in bundles if "stale" in b.node_ids), None)
        self.assertIsNotNone(bundle)

        probe = RuptureProbe()
        result = probe.probe(bundle, graph, ctx)
        self.assertEqual(result.verdict, "break")
        self.assertGreater(result.opposing_strength, 1.0)

    def test_fresh_approved_node_survives(self):
        """
        新鮮で高承認のノードは RuptureProbe が survive を返す。
        confidence boost に寄与する。
        """
        graph = MBGraph()
        node = _make_node(
            "fresh",
            exact_keys=["有効なルール"],
            success_count=10,
            approval_count=8,
        )
        graph.add_or_update(node)

        efp = _make_efp("有効なルール")
        ctx = _make_ctx(efp)
        locator = RelationConstraintLocator()
        bundles = locator.locate(graph, ctx)
        bundle = next((b for b in bundles if "fresh" in b.node_ids), None)
        self.assertIsNotNone(bundle)

        probe = RuptureProbe()
        result = probe.probe(bundle, graph, ctx)
        self.assertEqual(result.verdict, "survive")
        self.assertEqual(result.opposing_strength, 0.0)


class TestDissipateInversion(unittest.TestCase):

    def test_high_inertia_cools_slower(self):
        """
        慣性が高いノードほど H の散逸が遅い（反転確認）。
        BASE v2.0 設計: 強く結晶化した構造への反証は H が下がりにくく再検査へ至る。
        """
        h_state = HState(theta_0=10.0, gamma=0.1)

        # 2ノードに同量の熱を蓄積
        h_state.node_heats["low_inertia"] = __import__(
            "rdl_enterprise.h_state", fromlist=["HeatVector"]
        ).HeatVector(prediction=1.0, input_err=0.0)
        h_state.node_heats["high_inertia"] = __import__(
            "rdl_enterprise.h_state", fromlist=["HeatVector"]
        ).HeatVector(prediction=1.0, input_err=0.0)

        inertias = {
            "low_inertia": 0.2,   # 低慣性
            "high_inertia": 5.0,  # 高慣性
        }
        h_state.dissipate(inertias)

        # 高慣性のノードほど熱が残るはず
        h_low = h_state.node_heats["low_inertia"].prediction
        h_high = h_state.node_heats["high_inertia"].prediction

        self.assertGreater(h_high, h_low,
            "高慣性ノードの H は低慣性ノードより高いままであるべき（散逸が遅い）")


class TestConstraintBoostInCascade(unittest.TestCase):

    def test_fresh_node_confidence_higher_than_stale(self):
        """
        新鮮なノードと古いノードで同じ query にマッチしたとき、
        新鮮なノードの方が confidence が高くなる（constraint_boost の効果）。
        「古参ルールが100回成功でも、制度変更後は confidence boost を受けない」。
        """
        old_date = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()

        # 新鮮なグラフ（通常）
        graph_fresh = MBGraph()
        node_fresh = _make_node(
            "node_fresh",
            exact_keys=["経費精算の手続き"],
            confidence=0.7,
            success_count=5,
            approval_count=3,
        )
        graph_fresh.add_or_update(node_fresh)

        # 古いグラフ（同じ inertia に見えても freshness が低い）
        graph_stale = MBGraph()
        node_stale = _make_node(
            "node_stale",
            exact_keys=["経費精算の手続き"],
            confidence=0.7,
            success_count=100,
            approval_count=80,
            last_updated=old_date,
        )
        graph_stale.add_or_update(node_stale)

        efp = _make_efp("経費精算の手続き")

        cascade_fresh = InterpCascade(graph_fresh)
        cascade_stale = InterpCascade(graph_stale)

        pred_fresh = cascade_fresh.interpret(efp)
        pred_stale = cascade_stale.interpret(efp)

        # 古いノードは freshness が低いため constraint_score が低く → boost が小さい or 0
        # 新鮮なノードは survive → boost が加算される
        self.assertGreaterEqual(
            pred_fresh.confidence, pred_stale.confidence,
            "新鮮なノードの confidence は古いノード以上であるべき（constraint_boost の効果）"
        )


class TestFrozenContextConstraintIntegration(unittest.TestCase):
    """
    FrozenInterpretationContext における ConstraintConfig と constraint_evaluation_time の凍結保証
    (T0 SPEC 4, 6.1 / BASE v2.0 §4.2)
    """

    def test_frozen_context_includes_constraint_config_and_time_in_hash(self):
        from rdl_enterprise.snapshot import FrozenInterpretationContext

        graph = MBGraph()
        graph.freeze()
        custom_cfg = ConstraintConfig(constraint_boost_cap=0.08, w_freshness=0.5)
        eval_time = datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc)

        ctx = FrozenInterpretationContext(
            mb_version="v1.0",
            mb_content_hash="dummy_hash",
            frozen_mb=graph,
            constraint_config=custom_cfg,
            constraint_evaluation_time=eval_time,
        )

        self.assertIsNotNone(ctx.context_hash)
        self.assertTrue(len(ctx.context_hash) > 0)
        self.assertEqual(ctx.constraint_evaluation_time, eval_time)
        self.assertEqual(ctx.constraint_config.constraint_boost_cap, 0.08)

        # isolated cascade に伝播すること
        cascade = ctx.create_isolated_cascade()
        self.assertEqual(cascade.constraint_evaluation_time, eval_time)
        self.assertEqual(cascade.constraint_locator.config.constraint_boost_cap, 0.08)

    def test_custom_constraint_config_injection_respected_in_boost(self):
        """
        InterpCascade に注入されたカスタム ConstraintConfig が
        _constraint_boost 時に正しく適用されること (boost cap の制限)
        """
        graph = MBGraph()
        node = _make_node(
            "node_cap",
            exact_keys=["テスト用クエリ"],
            confidence=0.7,
            success_count=10,
            approval_count=8,
        )
        graph.add_or_update(node)
        efp = _make_efp("テスト用クエリ")

        # cap を極小 (0.02) に設定したカスタム config
        custom_cfg = ConstraintConfig(constraint_boost_cap=0.02)
        cascade = InterpCascade(graph, constraint_config=custom_cfg)

        pred = cascade.interpret(efp)
        # boost 分は最大でも 0.02 に抑えられているはず (confidence <= 0.72)
        self.assertLessEqual(pred.confidence, 0.72 + 1e-6)


if __name__ == "__main__":
    unittest.main()

