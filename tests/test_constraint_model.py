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

        stale_bundle = next((b for b in bundles if b.primary_node_id() == "stale_node"), None)
        fresh_bundle = next((b for b in bundles if b.primary_node_id() == "fresh_node"), None)

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
        from rdl_enterprise.snapshot import FeedbackResult, RelationProvenance

        # 人間管理者による拒絶フィードバック (明示的な制度的・管理者権限 Provenance)
        feedback_human = FeedbackResult(
            user_resolved=False,
            human_approved=False,
            human_rejected=True,
            feedback_comment="新方針によりこのルールは即時無効",
            provenance=RelationProvenance(
                source_type="admin",
                authority_level="human_only",
                is_authoritative=True,
            ),
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

    def test_feedback_result_default_provenance_no_authority_forging(self):
        """公理 B5: FeedbackResult のデフォルト provenance は権限を捏造しないこと"""
        from rdl_enterprise.snapshot import FeedbackResult
        from rdl_enterprise.constraint import compute_efp_prime_constraint

        # 1. 人間差し戻し（未指定時）
        fb_rej = FeedbackResult(user_resolved=False, human_rejected=True)
        self.assertEqual(fb_rej.provenance.source_type, "human_feedback")
        self.assertEqual(fb_rej.provenance.authority_level, "unknown")
        self.assertFalse(fb_rej.provenance.is_authoritative)

        c_rej = compute_efp_prime_constraint(fb_rej)
        # human_feedback: 0.70
        self.assertEqual(c_rej, 0.70)

        # 2. 一般ユーザー（未指定時）
        fb_user = FeedbackResult(user_resolved=True)
        self.assertEqual(fb_user.provenance.source_type, "user")
        self.assertEqual(fb_user.provenance.authority_level, "auto")
        self.assertFalse(fb_user.provenance.is_authoritative)
        c_user = compute_efp_prime_constraint(fb_user)
        self.assertEqual(c_user, 0.40)

    def test_time_concept_separation_frozen_mb_vs_efp_prime_observation(self):
        """時刻概念の分離: M_B側の解釈拘束時刻 (t) と EFP'側の観測時刻 (t+Δ) の直交分離"""
        from datetime import datetime, timezone, timedelta
        from rdl_enterprise.snapshot import FeedbackResult, RelationProvenance
        from rdl_enterprise.runtime import EnterpriseRuntime
        from rdl_enterprise.constraint import compute_efp_prime_constraint

        runtime = EnterpriseRuntime(theta_0=2.0)
        # 10日前に作成されたノード
        t_dispatch = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
        node = MBNode(
            id="node_time_test",
            domain="general",
            trigger_pattern={"exact_keys": ["時刻検証"]},
            action_template={"type": "direct_reply", "payload": "OK"},
            last_updated=t_dispatch - timedelta(days=10),
            confidence=0.8,
            approval_count=5,
        )
        runtime.mb_graph.add_or_update(node)

        # 1. チケットディスパッチ (時刻 t_dispatch で凍結)
        efp = _make_efp("時刻検証")
        runtime.dispatch_ticket(efp)
        snapshot = runtime.pending_snapshots[efp.ticket_id]

        # 凍結時刻を t_dispatch に設定
        object.__setattr__(snapshot.frozen_context, "constraint_evaluation_time", t_dispatch)

        # 2. 2時間後にフィードバック受領 (t_feedback = t_dispatch + 2時間)
        t_feedback = t_dispatch + timedelta(hours=2)
        snapshot.resolved_at = t_feedback.isoformat()

        # ケースA: フィードバック受領時点 (t_feedback) で観測された最新の反証
        fb_latest = FeedbackResult(
            user_resolved=False,
            human_rejected=True,
            provenance=RelationProvenance(
                source_type="admin",
                authority_level="human_only",
                observed_at=t_feedback,
                is_authoritative=True,
            ),
        )
        c_prime_latest = compute_efp_prime_constraint(fb_latest, snapshot, current_time=t_feedback)
        self.assertEqual(c_prime_latest, 1.0)

        # ケースB: フィードバック時点で「90日前の古い決定」を引用した反証
        t_old_observed = t_feedback - timedelta(days=90)
        fb_stale = FeedbackResult(
            user_resolved=False,
            human_rejected=True,
            provenance=RelationProvenance(
                source_type="admin",
                authority_level="human_only",
                observed_at=t_old_observed,
                is_authoritative=True,
            ),
        )
        # t_feedback から見て 90日経過 -> 半減期90日により time_factor = 0.5 -> C_prime = 0.5
        c_prime_stale = compute_efp_prime_constraint(fb_stale, snapshot, current_time=t_feedback)
        self.assertAlmostEqual(c_prime_stale, 0.5, places=2)

    def test_c_prime_scope_relative_constraint(self):
        """BASE v2.0: C_prime は絶対値ではなく「問い・管轄スコープとの関係」で相対的に立ち上がる"""
        from rdl_enterprise.snapshot import FeedbackResult, RelationProvenance, BusinessInput, CaseSnapshot, InterpretationPrediction
        from rdl_enterprise.constraint import compute_efp_prime_constraint

        # HR admin の来歴
        prov_hr_admin = RelationProvenance(
            source_type="admin",
            authority_level="human_only",
            authority_scope="hr",
        )
        fb = FeedbackResult(user_resolved=False, human_rejected=True, provenance=prov_hr_admin)

        # 1. 人事ドメインのチケット (管轄内)
        snap_hr = CaseSnapshot(
            efp=BusinessInput("T_HR_01", "U1", "hr", "有給休暇の申請"),
            f_pred=InterpretationPrediction(action_type="direct_reply", content="旧規定", confidence=0.8, matched_node_id="n1", cost_tier=1, domain="hr"),
        )
        c_prime_in_scope = compute_efp_prime_constraint(fb, snap_hr)
        # 管轄内なので 0.95
        self.assertEqual(c_prime_in_scope, 0.95)

        # 2. セキュリティ/インフラドメインのチケット (管轄外)
        snap_sec = CaseSnapshot(
            efp=BusinessInput("T_SEC_01", "U1", "security", "本番DBの決済API鍵"),
            f_pred=InterpretationPrediction(action_type="direct_reply", content="旧規定", confidence=0.8, matched_node_id="n2", cost_tier=1, domain="security"),
        )
        c_prime_out_of_scope = compute_efp_prime_constraint(fb, snap_sec)
        # 管轄外ペナルティ (scope_factor = 0.5) により半減
        self.assertAlmostEqual(c_prime_out_of_scope, 0.95 * 0.5, places=2)
        self.assertLess(c_prime_out_of_scope, 0.50)

    def test_c_prime_claim_type_relevance(self):
        """言明タイプ (claim_type) と情報源の適合性: 監査ログは事実記録に強く、主観意見には控えめ"""
        from rdl_enterprise.snapshot import FeedbackResult, RelationProvenance
        from rdl_enterprise.constraint import compute_efp_prime_constraint

        # 1. audit log による確定事実記録 (fact)
        prov_fact = RelationProvenance(
            source_type="audit",
            channel="audit_log",
            claim_type="fact",
        )
        fb_fact = FeedbackResult(user_resolved=False, human_rejected=True, provenance=prov_fact)
        c_fact = compute_efp_prime_constraint(fb_fact)
        # 0.90 + 0.10(audit_log) = 1.0 * 1.05 = 1.05 -> min(1.0) = 1.0
        self.assertEqual(c_fact, 1.0)

        # 2. audit による裁量・主観的意見 (judgment)
        prov_judgment = RelationProvenance(
            source_type="audit",
            channel="standard",
            claim_type="judgment",
        )
        fb_judgment = FeedbackResult(user_resolved=False, human_rejected=True, provenance=prov_judgment)
        c_judgment = compute_efp_prime_constraint(fb_judgment)
        # 0.90 * 0.80 = 0.72
        self.assertAlmostEqual(c_judgment, 0.72, places=2)
        self.assertLess(c_judgment, c_fact)

    def test_relation_dependent_freshness_decay(self):
        """関係相対的な時間減衰: 確定事実は古くても弱まらず、手続き規則やセッション状態は減衰する"""
        from datetime import datetime, timezone, timedelta
        from rdl_enterprise.snapshot import FeedbackResult, RelationProvenance
        from rdl_enterprise.constraint import compute_efp_prime_constraint

        now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
        one_year_ago = now - timedelta(days=365)

        # 1. 1年前の確定事実 (fact: ログや発生記録) -> 半減期 ∞、減衰なし
        prov_fact = RelationProvenance(
            source_type="audit",
            observed_at=one_year_ago,
            relation_type="fact",
        )
        fb_fact = FeedbackResult(user_resolved=False, human_rejected=True, provenance=prov_fact)
        c_fact = compute_efp_prime_constraint(fb_fact, current_time=now)
        # 減衰しない
        self.assertAlmostEqual(c_fact, 0.90, places=2)

        # 2. 1年前の通常の手続き規則 (rule: マニュアル等) -> 半減期 90日、約 4半減期経過
        prov_rule = RelationProvenance(
            source_type="audit",
            observed_at=one_year_ago,
            relation_type="rule",
        )
        fb_rule = FeedbackResult(user_resolved=False, human_rejected=True, provenance=prov_rule)
        c_rule = compute_efp_prime_constraint(fb_rule, current_time=now)
        # 0.90 * (0.5 ** (365/90)) = 0.90 * 0.0598 = 0.0538 -> max(0.1) = 0.10
        self.assertEqual(c_rule, 0.10)

        # 3. 10日前のセッション・リアルタイム状態 (ephemeral) -> 半減期 3日、3半減期以上経過
        ten_days_ago = now - timedelta(days=10)
        prov_ephemeral = RelationProvenance(
            source_type="senior",
            authority_level="require_approval",
            observed_at=ten_days_ago,
            relation_type="ephemeral",
        )
        fb_ephemeral = FeedbackResult(user_resolved=False, human_rejected=True, provenance=prov_ephemeral)
        c_ephemeral = compute_efp_prime_constraint(fb_ephemeral, current_time=now)
        # 0.85 * (0.5 ** (10/3)) = 0.85 * 0.099 = 0.084 -> max(0.1) = 0.10
        self.assertEqual(c_ephemeral, 0.10)

    def test_multi_node_constraint_bundle(self):
        """関係の束 (ConstraintBundle): 同一ドメインの共起・支援ノードが束ねられること"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode
        from rdl_enterprise.constraint import RelationConstraintLocator, ConstraintContext

        graph = MBGraph()
        n1 = MBNode(id="node_pay_1", domain="finance", trigger_pattern={"exact_keys": ["請求書支払"]}, action_template={"type": "direct_reply", "payload": "A"})
        n2 = MBNode(id="node_pay_2", domain="finance", trigger_pattern={"exact_keys": ["請求書支払", "振込"]}, action_template={"type": "direct_reply", "payload": "B"})
        n3 = MBNode(id="node_pay_3", domain="finance", trigger_pattern={"exact_keys": ["経費精算"]}, action_template={"type": "direct_reply", "payload": "C"})
        n4 = MBNode(id="node_hr_1", domain="hr", trigger_pattern={"exact_keys": ["有給休暇"]}, action_template={"type": "direct_reply", "payload": "D"})

        graph.add_or_update(n1)
        graph.add_or_update(n2)
        graph.add_or_update(n3)
        graph.add_or_update(n4)

        locator = RelationConstraintLocator()
        efp = _make_efp("請求書支払の手順")
        ctx = ConstraintContext(efp=efp, active_domain="finance")

        bundle = locator.locate_bundle_for_node(graph, n1, ctx)
        self.assertEqual(bundle.primary_node_id(), "node_pay_1")
        # 同一ドメインでキーまたはアクションを共有するノード群が束ねられている
        self.assertGreater(len(bundle.node_ids), 1)
        self.assertIn("node_pay_2", bundle.supporting_node_ids)
        # 他ドメイン(hr)のノードは束に含まれない
        self.assertNotIn("node_hr_1", bundle.node_ids)


    def test_multi_node_bundle_no_irrelevant_action_type_inclusion(self):
        """同一 action_type を持つだけの無関係ノードが束に混入しないこと (bundling純化)"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode
        from rdl_enterprise.constraint import RelationConstraintLocator, ConstraintContext

        graph = MBGraph()
        n_main = MBNode(id="n_tax", domain="finance", trigger_pattern={"exact_keys": ["法人税"]}, action_template={"type": "direct_reply", "payload": "税率回答"})
        n_unrelated = MBNode(id="n_lunch", domain="finance", trigger_pattern={"exact_keys": ["社食代補助"]}, action_template={"type": "direct_reply", "payload": "補助回答"})
        graph.add_or_update(n_main)
        graph.add_or_update(n_unrelated)

        locator = RelationConstraintLocator()
        efp = _make_efp("法人税の申告")
        ctx = ConstraintContext(efp=efp, active_domain="finance")

        bundle = locator.locate_bundle_for_node(graph, n_main, ctx)
        self.assertEqual(bundle.primary_node_id(), "n_tax")
        # direct_reply が同じでも、キーを共有しない無関係ノードは束に含まれないこと
        self.assertNotIn("n_lunch", bundle.node_ids)

    def test_bundle_constraint_score_synergy(self):
        """支援ノード群による相乗効果（synergy boost）が束の総合拘束スコアを高めること"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode
        from rdl_enterprise.constraint import RelationConstraintLocator, ConstraintContext

        # 孤立した単独ノードグラフ
        graph_solo = MBGraph()
        n_solo = MBNode(id="n_solo", domain="finance", trigger_pattern={"exact_keys": ["海外送金"]}, action_template={"type": "direct_reply", "payload": "A"}, confidence=0.7, approval_count=5)
        graph_solo.add_or_update(n_solo)

        # 相互補強する支援ノードが存在するグラフ
        graph_bundle = MBGraph()
        n_base = MBNode(id="n_base", domain="finance", trigger_pattern={"exact_keys": ["海外送金"]}, action_template={"type": "direct_reply", "payload": "A"}, confidence=0.7, approval_count=5)
        n_supp = MBNode(id="n_supp", domain="finance", trigger_pattern={"exact_keys": ["海外送金", "SWIFTコード"]}, action_template={"type": "direct_reply", "payload": "A"}, confidence=0.7, approval_count=10)
        graph_bundle.add_or_update(n_base)
        graph_bundle.add_or_update(n_supp)

        locator = RelationConstraintLocator()
        efp = _make_efp("海外送金の手数料")
        ctx = ConstraintContext(efp=efp, active_domain="finance")

        bundle_solo = locator.locate_bundle_for_node(graph_solo, n_solo, ctx)
        bundle_multi = locator.locate_bundle_for_node(graph_bundle, n_base, ctx)

        # 支援ノードの裏付けがある束の総合拘束スコアは単独ノードより高いこと
        self.assertGreater(bundle_multi.constraint_score, bundle_solo.constraint_score)

    def test_mb_graph_key_index_hot_path_fast_lookup(self):
        """MBGraph の _key_index による高速共起検索が正しく機能すること"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode

        graph = MBGraph()
        n1 = MBNode(id="n1", domain="tech", trigger_pattern={"exact_keys": ["git pull", "git merge"]}, action_template={"type": "direct_reply", "payload": "A"})
        n2 = MBNode(id="n2", domain="tech", trigger_pattern={"exact_keys": ["git push", "git pull"]}, action_template={"type": "direct_reply", "payload": "B"})
        n3 = MBNode(id="n3", domain="tech", trigger_pattern={"exact_keys": ["docker run"]}, action_template={"type": "direct_reply", "payload": "C"})
        graph.add_or_update(n1)
        graph.add_or_update(n2)
        graph.add_or_update(n3)

        related = graph.find_co_occurring_nodes(n1)
        related_ids = [r.id for r in related]
        self.assertIn("n2", related_ids)
        self.assertNotIn("n3", related_ids)
        self.assertNotIn("n1", related_ids)

    def test_bridge_node_detection_with_multi_node_bundles(self):
        """multi-node bundle が存在しても bridge ノードが重複生成されないこと"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode
        from rdl_enterprise.constraint import RelationConstraintLocator, ConstraintContext

        graph = MBGraph()
        # bridge ノードと共起ノード
        n_bridge = MBNode(id="n_bridge", domain="special", trigger_pattern={"exact_keys": ["極秘事項A", "極秘事項B"]}, action_template={"type": "direct_reply", "payload": "A"})
        n_supp = MBNode(id="n_supp", domain="special", trigger_pattern={"exact_keys": ["極秘事項A"]}, action_template={"type": "direct_reply", "payload": "A"})
        graph.add_or_update(n_bridge)
        graph.add_or_update(n_supp)

        locator = RelationConstraintLocator()
        efp = _make_efp("極秘事項Aの閲覧")
        ctx = ConstraintContext(efp=efp, active_domain="special")

        bundles = locator.locate(graph, ctx)
        bridge_bundles_for_n = [b for b in bundles if b.primary_node_id() == "n_bridge"]
        # n_bridge を代表とするバンドルは重複せず1つだけであること
        self.assertEqual(len(bridge_bundles_for_n), 1)

    def test_historical_fact_vs_current_state_decay(self):
        """確定過去事実 (historical_fact) は半減期∞で減衰せず、動的現在状態 (current_state) は急速に減衰すること"""
        from datetime import datetime, timezone, timedelta
        from rdl_enterprise.snapshot import FeedbackResult, RelationProvenance
        from rdl_enterprise.constraint import compute_efp_prime_constraint

        now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
        ten_days_ago = now - timedelta(days=10)

        # 1. 10日前の確定過去記録 (historical_fact: トランザクション完了ログ)
        prov_hist = RelationProvenance(
            source_type="audit",
            observed_at=ten_days_ago,
            relation_type="historical_fact",
        )
        fb_hist = FeedbackResult(user_resolved=False, human_rejected=True, provenance=prov_hist)
        c_hist = compute_efp_prime_constraint(fb_hist, current_time=now)
        # 減衰しない (0.90)
        self.assertAlmostEqual(c_hist, 0.90, places=2)

        # 2. 10日前の動的現在状態 (current_state: センサー温度・口座残高など)
        prov_current = RelationProvenance(
            source_type="audit",
            observed_at=ten_days_ago,
            relation_type="current_state",
        )
        fb_current = FeedbackResult(user_resolved=False, human_rejected=True, provenance=prov_current)
        c_current = compute_efp_prime_constraint(fb_current, current_time=now)
        # 半減期 1.0日、10日経過でほぼ消失 (0.90 * 0.5^10 ≈ 0.00088 -> max(0.1) = 0.10)
        self.assertEqual(c_current, 0.10)

    def test_authoritative_judgment_is_relativized(self):
        """is_authoritative=True でも claim_type="judgment" の場合は 1.0 に固定されず相対化されること"""
        from rdl_enterprise.snapshot import FeedbackResult, RelationProvenance
        from rdl_enterprise.constraint import compute_efp_prime_constraint

        # 公式機関による主観的意見・裁量判断 (judgment)
        prov_auth_judgment = RelationProvenance(
            source_type="oracle",
            is_authoritative=True,
            claim_type="judgment",
        )
        fb = FeedbackResult(user_resolved=False, human_rejected=True, provenance=prov_auth_judgment)
        c = compute_efp_prime_constraint(fb)
        # 1.0 固定ではなく claim_factor (0.80) が適用される
        self.assertAlmostEqual(c, 0.80, places=2)
        self.assertLess(c, 1.0)

    def test_rupture_probe_bundle_level_resilience(self):
        """未承認の代表ノードでも、高承認の支援ノードが存在すれば束として survive すること"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode
        from rdl_enterprise.constraint import RelationConstraintLocator, RuptureProbe, ConstraintContext

        graph = MBGraph()
        # 代表ノードは未承認 (approval_count = 0)
        n_primary = MBNode(id="n_new_flow", domain="sales", trigger_pattern={"exact_keys": ["新規見積作成"]}, action_template={"type": "direct_reply", "payload": "見積書フォーマット"}, confidence=0.6, approval_count=0)
        # 支援ノードは高承認 (approval_count = 10)
        n_supp = MBNode(id="n_old_flow", domain="sales", trigger_pattern={"exact_keys": ["新規見積作成", "割引率"]}, action_template={"type": "direct_reply", "payload": "見積書フォーマット"}, confidence=0.8, approval_count=10)
        graph.add_or_update(n_primary)
        graph.add_or_update(n_supp)

        locator = RelationConstraintLocator()
        efp = _make_efp("新規見積作成の手順", category="sales")
        ctx = ConstraintContext(efp=efp, active_domain="sales")

        bundle = locator.locate_bundle_for_node(graph, n_primary, ctx)
        probe = RuptureProbe()
        result = probe.probe(bundle, graph, ctx)

        # 支援ノードの承認実績による相互補強・冗長性で survive すること
        self.assertEqual(result.verdict, "survive")

    def test_rupture_probe_bundle_internal_fissure_unresolved(self):
        """束の構成ノード間でアクションが対立している場合、内部亀裂として unresolved (ξ) になること"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode
        from rdl_enterprise.constraint import ConstraintBundle, RuptureProbe, ConstraintContext

        graph = MBGraph()
        n1 = MBNode(id="n1", domain="sales", trigger_pattern={"exact_keys": ["割引"]}, action_template={"type": "direct_reply", "payload": "即時承認"}, confidence=0.8, approval_count=5)
        n2 = MBNode(id="n2", domain="sales", trigger_pattern={"exact_keys": ["割引"]}, action_template={"type": "direct_reply", "payload": "部長決裁必須"}, confidence=0.8, approval_count=5)
        graph.add_or_update(n1)
        graph.add_or_update(n2)

        # n1 と n2 が相容れないアクションを持つにもかかわらず同束に存在する場合
        bundle = ConstraintBundle(
            node_ids=["n1", "n2"],
            locus_type="strong",
            constraint_score=0.8,
            relevance=0.8,
            freshness=0.9,
            authority_weight=0.4,
            source_strength=0.8,
        )

        efp = _make_efp("割引の承認ルール", category="sales")
        ctx = ConstraintContext(efp=efp, active_domain="sales")

        probe = RuptureProbe()
        result = probe.probe(bundle, graph, ctx)

        # 束内部の対立により unresolved になること
        self.assertEqual(result.verdict, "unresolved")
        self.assertIn("束内部", result.rupture_reason)


    def test_supporting_nodes_selection_is_deterministic(self):
        """候補が多数ある場合でも、find_co_occurring_nodes が完全に決定的な順序で選出すること"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode

        graph = MBGraph()
        n_main = MBNode(id="n_main", domain="tech", trigger_pattern={"exact_keys": ["デプロイ"]}, action_template={"type": "direct_reply", "payload": "OK"})
        graph.add_or_update(n_main)

        # 10個のノードを追加 (承認数やIDをバラバラに設定)
        for i in range(10):
            node = MBNode(
                id=f"node_{i:02d}",
                domain="tech",
                trigger_pattern={"exact_keys": ["デプロイ", f"サブ_{i}"]},
                action_template={"type": "direct_reply", "payload": f"OK_{i}"},
                approval_count=i * 2,
                confidence=0.5 + (i * 0.04),
            )
            graph.add_or_update(node)

        # 複数回呼び出して完全に同一のID順列が返ることを確認
        res1 = [n.id for n in graph.find_co_occurring_nodes(n_main, limit=4)]
        res2 = [n.id for n in graph.find_co_occurring_nodes(n_main, limit=4)]
        self.assertEqual(res1, res2)
        # 承認数降順・confidence降順により、上位は node_09, node_08, node_07, node_06 であること
        self.assertEqual(res1, ["node_09", "node_08", "node_07", "node_06"])

    def test_stale_or_rejected_support_node_cannot_boost_survive(self):
        """陳腐化または大量拒絶された支援ノードは健全性検査で除外され、未承認ノードを survive させないこと"""
        from datetime import datetime, timezone, timedelta
        from rdl_enterprise.mb_graph import MBGraph, MBNode
        from rdl_enterprise.constraint import RelationConstraintLocator, RuptureProbe, ConstraintContext

        now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
        graph = MBGraph()
        # 代表ノード: 未承認
        n_primary = MBNode(
            id="n_prim",
            domain="ops",
            trigger_pattern={"exact_keys": ["サーバー再起動"]},
            action_template={"type": "direct_reply", "payload": "再起動手順"},
            confidence=0.6,
            approval_count=0,
            last_updated=now.isoformat(),
        )
        # 支援ノードA: 承認数100だが、500日前の更新（freshness < 0.2 で陳腐化）
        n_stale = MBNode(
            id="n_stale",
            domain="ops",
            trigger_pattern={"exact_keys": ["サーバー再起動", "緊急"]},
            action_template={"type": "direct_reply", "payload": "再起動手順"},
            confidence=0.8,
            approval_count=100,
            last_updated=(now - timedelta(days=500)).isoformat(),
        )
        # 支援ノードB: 承認数100だが、差し戻し80（rejection_ratio = 80/180 = 0.44 >= 0.4 で拒絶多数）
        n_rejected = MBNode(
            id="n_rejected",
            domain="ops",
            trigger_pattern={"exact_keys": ["サーバー再起動", "通常"]},
            action_template={"type": "direct_reply", "payload": "再起動手順"},
            confidence=0.8,
            approval_count=100,
            rejection_count=80,
            last_updated=now.isoformat(),
        )
        graph.add_or_update(n_primary)
        graph.add_or_update(n_stale)
        graph.add_or_update(n_rejected)

        locator = RelationConstraintLocator()
        efp = _make_efp("サーバー再起動の手順", category="ops")
        ctx = ConstraintContext(efp=efp, current_time=now, active_domain="ops")

        bundle = locator.locate_bundle_for_node(graph, n_primary, ctx)
        probe = RuptureProbe()
        result = probe.probe(bundle, graph, ctx)

        # 健全な支援ノードが存在しないため、未検証として unresolved (ξ) に留まること
        self.assertEqual(result.verdict, "unresolved")

    def test_support_lineage_duplicate_suppression(self):
        """同一 source_lineage からの複製ノードは synergy が抑制され、独立関係源のみが相乗効果を持つこと"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode
        from rdl_enterprise.constraint import RelationConstraintLocator, ConstraintContext

        # グラフA: 同一マニュアルから複製されたノード群
        graph_dup = MBGraph()
        n_base_dup = MBNode(id="n_b1", domain="hr", trigger_pattern={"exact_keys": ["育休"]}, action_template={"type": "direct_reply", "payload": "A"}, confidence=0.7, approval_count=5, source_lineage="manual_hr_v1")
        n_s1_dup = MBNode(id="n_s1", domain="hr", trigger_pattern={"exact_keys": ["育休", "給付金"]}, action_template={"type": "direct_reply", "payload": "A"}, confidence=0.7, approval_count=5, source_lineage="manual_hr_v1")
        n_s2_dup = MBNode(id="n_s2", domain="hr", trigger_pattern={"exact_keys": ["育休", "申請書"]}, action_template={"type": "direct_reply", "payload": "A"}, confidence=0.7, approval_count=5, source_lineage="manual_hr_v1")
        graph_dup.add_or_update(n_base_dup)
        graph_dup.add_or_update(n_s1_dup)
        graph_dup.add_or_update(n_s2_dup)

        # グラフB: 独立した関係源（法務決定、監査ログ）からのノード群
        graph_indep = MBGraph()
        n_base_ind = MBNode(id="n_b2", domain="hr", trigger_pattern={"exact_keys": ["育休"]}, action_template={"type": "direct_reply", "payload": "A"}, confidence=0.7, approval_count=5, source_lineage="manual_hr_v1")
        n_s1_ind = MBNode(id="n_s1_ind", domain="hr", trigger_pattern={"exact_keys": ["育休", "給付金"]}, action_template={"type": "direct_reply", "payload": "A"}, confidence=0.7, approval_count=5, source_lineage="audit_log_2026")
        n_s2_ind = MBNode(id="n_s2_ind", domain="hr", trigger_pattern={"exact_keys": ["育休", "申請書"]}, action_template={"type": "direct_reply", "payload": "A"}, confidence=0.7, approval_count=5, source_lineage="labor_law_amendment")
        graph_indep.add_or_update(n_base_ind)
        graph_indep.add_or_update(n_s1_ind)
        graph_indep.add_or_update(n_s2_ind)

        locator = RelationConstraintLocator()
        efp = _make_efp("育休の申請手続き", category="hr")
        ctx = ConstraintContext(efp=efp, active_domain="hr")

        bundle_dup = locator.locate_bundle_for_node(graph_dup, n_base_dup, ctx)
        bundle_indep = locator.locate_bundle_for_node(graph_indep, n_base_ind, ctx)

        # 独立した系譜からの支持を持つ bundle_indep の方が総合拘束スコアが高いこと
        self.assertGreater(bundle_indep.constraint_score, bundle_dup.constraint_score)

    def test_actual_bundle_removal_perturbation(self):
        """実効的バンドル除去切断摂動: 束を切断したときに潜在対向ノードが露出すれば break と判定されること"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode
        from rdl_enterprise.constraint import RelationConstraintLocator, RuptureProbe, ConstraintContext

        graph = MBGraph()
        # 既存バンドル（代表ノード: 振込を通常回答）
        n_normal = MBNode(
            id="n_normal",
            domain="finance",
            trigger_pattern={"exact_keys": ["送金振込"]},
            action_template={"type": "direct_reply", "payload": "通常振込手続"},
            confidence=0.70,
            approval_count=5,
        )
        # 潜在対向ノード（同じキーだが、より高い confidence で異なるアクションを要求）
        n_compliance = MBNode(
            id="n_compliance",
            domain="finance",
            trigger_pattern={"exact_keys": ["送金振込"]},
            action_template={"type": "escalate_to_aml", "payload": "AMLコンプライアンス調査必須"},
            confidence=0.90,
            approval_count=20,
        )
        graph.add_or_update(n_normal)
        graph.add_or_update(n_compliance)

        locator = RelationConstraintLocator()
        efp = _make_efp("送金振込", category="finance")
        ctx = ConstraintContext(efp=efp, active_domain="finance")

        bundle = locator.locate_bundle_for_node(graph, n_normal, ctx)
        bundle.node_ids = ["n_normal"]

        probe = RuptureProbe()
        result = probe.probe(bundle, graph, ctx)

        # 実効的切断摂動により、潜在対向ノード n_compliance との衝突が露出し break すること
        self.assertEqual(result.verdict, "break")
        self.assertIn("実効的バンドル切断", result.rupture_reason)

    def test_authoritative_general_claim_is_not_full_1_0(self):
        """公式機関による表明であっても claim_type="general"（一般広報等）は 1.0 に固定されず 0.90 に抑制されること"""
        from rdl_enterprise.snapshot import FeedbackResult, RelationProvenance
        from rdl_enterprise.constraint import compute_efp_prime_constraint

        prov_general = RelationProvenance(
            source_type="oracle",
            is_authoritative=True,
            channel="standard",
            claim_type="general",
        )
        fb_gen = FeedbackResult(user_resolved=False, human_rejected=True, provenance=prov_general)
        c_gen = compute_efp_prime_constraint(fb_gen)
        # 1.0 ではなく 0.90 に抑制されていること
        self.assertAlmostEqual(c_gen, 0.90, places=2)


if __name__ == "__main__":
    unittest.main()


