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
        # node.confidence 0.7 に対し、boost 分は最大でも 0.02 に抑えられているはず (confidence <= 0.72)
        self.assertLessEqual(pred.confidence, 0.72 + 1e-6)


class TestPerturbationAndOpposingConstraint(unittest.TestCase):
    """
    RuptureProbe の摂動検査 (B4/B5: 未検査は unresolved、揺らして耐えたもののみ survive)
    および C_old × C_prime による対向拘束強度の検証 (BASE v2.0 §4.2)
    """

    def test_relation_provenance_c_prime_evaluation(self):
        """
        後続関係の来歴 (RelationProvenance) が C_prime に厳密に反映されること。
        - 制度的公式記録 (is_authoritative=True): 1.0
        - 管理者オーバーライド (admin + admin_override): 0.95 + 0.10 -> 1.0
        - 監査ログ (audit + audit_log): 0.90 + 0.10 -> 1.0
        - 一般ユーザーの通常フィードバック: 0.40
        """
        from rdl_enterprise.snapshot import RelationProvenance, FeedbackResult
        from rdl_enterprise.constraint import compute_efp_prime_constraint

        # 公式記録・オラクル
        fb_authoritative = FeedbackResult(
            user_resolved=False,
            provenance=RelationProvenance(
                source_type="oracle",
                is_authoritative=True,
                channel="official_doc",
            ),
        )
        self.assertEqual(compute_efp_prime_constraint(fb_authoritative), 1.0)

        # 管理者の是正命令
        fb_admin = FeedbackResult(
            user_resolved=False,
            correction_content="新制度条文第4条に基づく差し戻し",
            provenance=RelationProvenance(
                source_type="admin",
                authority_level="human_only",
                channel="admin_override",
            ),
        )
        self.assertGreaterEqual(compute_efp_prime_constraint(fb_admin), 0.95)

        # 一般ユーザーの通常フィードバック（来歴権限なし）
        fb_user = FeedbackResult(
            user_resolved=True,
            provenance=RelationProvenance(
                source_type="user",
                authority_level="auto",
                channel="standard",
            ),
        )
        self.assertLess(compute_efp_prime_constraint(fb_user), 0.6)


    def test_unproven_node_defaults_to_unresolved(self):
        """
        承認実績（approval_count < 3）がなく、制度的権限もない未検証ノードは、
        破断もしていないが survive にもならず unresolved になる。
        その結果、confidence boost は付与されない。
        """
        graph = MBGraph()
        node = _make_node(
            "unproven_rule",
            exact_keys=["新しい社内手続"],
            confidence=0.6,
            success_count=1,
            approval_count=0,  # 未承認
        )
        graph.add_or_update(node)
        efp = _make_efp("新しい社内手続")
        ctx = _make_ctx(efp)

        locator = RelationConstraintLocator()
        bundle = locator.locate_bundle_for_node(graph, node, ctx)
        self.assertIsNotNone(bundle)

        probe = RuptureProbe()
        result = probe.probe(bundle, graph, ctx)
        # 未検証ノードは安易に survive と呼ばず unresolved になること！
        self.assertEqual(result.verdict, "unresolved")

        # cascade での解釈時にも boost が加算されないこと (confidence == 0.6)
        cascade = InterpCascade(graph)
        pred = cascade.interpret(efp)
        self.assertAlmostEqual(pred.confidence, 0.6, places=4)

    def test_domain_boundary_perturbation_detects_cross_domain_conflict(self):
        """
        境界拡張摂動：ドメイン境界 B (hr) のノードに対し、他ドメイン (security) に
        同一キーで異なる結論を持つ競合ノードが存在する場合、
        ドメイン境界の壁を取り払う摂動によって競合が露出し、break と判定される。
        """
        graph = MBGraph()
        node_hr = _make_node(
            "hr_pass",
            domain="hr",
            exact_keys=["パスワード再発行"],
            confidence=0.7,
            approval_count=10,
        )
        # security ドメインに異なる結論のルールが存在
        node_sec = MBNode(
            id="sec_pass",
            domain="security",
            trigger_pattern={"exact_keys": ["パスワード再発行"], "rule_expr": None},
            action_template={"type": "escalate_to_soc", "payload": "SOCへ緊急エスカレーション"},
            authority_level="human_only",
            confidence=0.9,
            approval_count=15,
        )
        graph.add_or_update(node_hr)
        graph.add_or_update(node_sec)

        efp = _make_efp("パスワード再発行", category="hr")
        ctx = _make_ctx(efp)

        locator = RelationConstraintLocator()
        bundle = locator.locate_bundle_for_node(graph, node_hr, ctx)
        probe = RuptureProbe()
        result = probe.probe(bundle, graph, ctx)

        # 境界拡張摂動により競合拘束が露出して破断 (break) すること
        self.assertEqual(result.verdict, "break")
        self.assertIn("境界拡張摂動", result.rupture_reason)
        self.assertGreaterEqual(result.opposing_strength, 1.5)

    def test_domain_boundary_perturbation_uses_relational_constraint_not_confidence(self):
        """
        境界拡張摂動の比較軸純化テスト (BASE v2.0):
        ノード A (hr) は confidence=0.95 と高いが、古い・未承認で関係拘束強度 C_rel は低い。
        他ドメインのノード B (security) は confidence=0.70 と A より低いが、
        human_only 権限・高承認・最新で関係拘束強度 C_rel は高い。
        旧実装 (confidence比較) では見逃されていたが、
        新実装 (C_rel比較) では B の強い関係拘束が A を圧倒し、正しく break を検出する。
        """
        old_date = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
        graph = MBGraph()
        # confidence は高いが、古く未承認のルール
        node_a = _make_node(
            "node_a_hr",
            domain="hr",
            exact_keys=["APIキーの発行"],
            confidence=0.95,
            approval_count=0,
            last_updated=old_date,
        )
        # confidence は低いが、制度的権限・高承認・新鮮なルール
        node_b = MBNode(
            id="node_b_sec",
            domain="security",
            trigger_pattern={"exact_keys": ["APIキーの発行"], "rule_expr": None},
            action_template={"type": "security_approval_flow", "payload": "情報セキュリティ部門承認必須"},
            authority_level="human_only",
            confidence=0.70,  # A (0.95) より低い！
            approval_count=20,
            last_updated=datetime.now(timezone.utc).isoformat(),
        )
        graph.add_or_update(node_a)
        graph.add_or_update(node_b)

        efp = _make_efp("APIキーの発行", category="hr")
        ctx = _make_ctx(efp)

        locator = RelationConstraintLocator()
        bundle_a = locator.locate_bundle_for_node(graph, node_a, ctx)
        probe = RuptureProbe()
        result = probe.probe(bundle_a, graph, ctx)

        # confidence が低くても C_rel の高い B によって破断されること
        self.assertEqual(result.verdict, "break")
        self.assertIn("C_rel", result.rupture_reason)

    def test_c_prime_opposing_strength_boost_on_authoritative_conflict(self):
        """
        C_old (既存高拘束ノード) と C_prime (人間・管理者による明示的反証) が衝突したとき、
        実効対向拘束強度 opposing_strength が大きく跳ね上がること。
        (BASE v2.0: 強い既存ルールと強い後続記録の衝突は H を激しく保持する)
        """
        from rdl_enterprise.constraint import (
            compute_efp_prime_constraint,
            compute_opposing_conflict_strength,
        )
        from rdl_enterprise.snapshot import FeedbackResult

        # 人間管理者による拒絶フィードバック
        feedback_human = FeedbackResult(
            user_resolved=False,
            human_approved=False,
            human_rejected=True,
            feedback_comment="新方針によりこのルールは即時無効",
        )
        c_prime = compute_efp_prime_constraint(feedback_human)
        self.assertEqual(c_prime, 1.0, "人間管理者の拒絶は C_prime=1.0")

        # 既存ノードが強固 (C_old = 0.8) である場合
        c_old_strong = 0.8
        opposing_strong = compute_opposing_conflict_strength(c_old_strong, c_prime, has_conflict=True)
        # 1.0 + (0.8 * 1.0) * 2.0 = 2.6
        self.assertGreater(opposing_strong, 2.5)

        # 逆に既存ノードが仮ルール (C_old = 0.1) の場合
        c_old_weak = 0.1
        opposing_weak = compute_opposing_conflict_strength(c_old_weak, c_prime, has_conflict=True)
        self.assertLess(opposing_weak, 1.5)

        self.assertGreater(opposing_strong, opposing_weak,
            "強い既存拘束と衝突した方が対向拘束強度 (発熱重み) が遥かに高くなるべき")

    def test_runtime_resolve_uses_frozen_mb_for_rupture_check(self):
        """
        T0 代謝規律：
        dispatch 完了後、live な mb_graph のノードが外部から変更・削除されても、
        feedback 時の拘束検査 (RuptureProbe) は dispatch 時点の frozen_mb を対象に行われること。
        """
        from rdl_enterprise.runtime import EnterpriseRuntime
        from rdl_enterprise.snapshot import FeedbackResult

        runtime = EnterpriseRuntime()
        node = _make_node(
            "node_frozen_test",
            exact_keys=["凍結検証用クエリ"],
            confidence=0.8,
            success_count=5,
            approval_count=5,
        )
        runtime.mb_graph.add_or_update(node)

        efp = _make_efp("凍結検証用クエリ")
        dispatch_res = runtime.dispatch_ticket(efp)
        self.assertEqual(dispatch_res.prediction.matched_node_id, "node_frozen_test")

        # dispatch 後、live な mb_graph 側でノードを削除してしまう（意図的な live 改変）
        runtime.mb_graph.nodes.pop("node_frozen_test", None)
        self.assertIsNone(runtime.mb_graph.get("node_frozen_test"))

        # feedback を解決
        feedback = FeedbackResult(
            user_resolved=True,
            human_approved=True,
            feedback_comment="問題なく解決",
        )
        # live からノードが消えていても、frozen_mb から旧ノードの拘束が正しく取得・評価され、
        # 例外なく正常に resolution が完了すること
        res = runtime.resolve_ticket_feedback(efp.ticket_id, feedback)
        self.assertIsNotNone(res)
        self.assertEqual(res.status.value, "success")


if __name__ == "__main__":
    unittest.main()


