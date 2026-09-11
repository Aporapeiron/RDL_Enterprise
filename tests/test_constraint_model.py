"""
test_constraint_model.py ― Relational Constraint モデルのテスト
(BASE v2.0 §4.2 / SPEC v2.0 §6.2 整合)
"""
import unittest
from datetime import datetime, timezone, timedelta

from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
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
    last_support_at: str = None,
    last_opposing_at: str = None,
) -> MBNode:
    if last_support_at is None and last_updated is not None:
        last_support_at = last_updated
    elif last_support_at is None and last_updated is None:
        last_support_at = datetime.utcnow().isoformat()
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
        last_support_at=last_support_at,
        last_opposing_at=last_opposing_at,
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
        graph.commit_node(node, origin=CommitmentOrigin.TEST_FIXTURE)

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
        graph.commit_node(stale_node, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(fresh_node, origin=CommitmentOrigin.TEST_FIXTURE)

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
        graph.commit_node(bridge_node, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(other_node, origin=CommitmentOrigin.TEST_FIXTURE)

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
        graph.commit_node(node, origin=CommitmentOrigin.TEST_FIXTURE)

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
        graph.commit_node(node, origin=CommitmentOrigin.TEST_FIXTURE)

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
        graph_fresh.commit_node(node_fresh, origin=CommitmentOrigin.TEST_FIXTURE)

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
        graph_stale.commit_node(node_stale, origin=CommitmentOrigin.TEST_FIXTURE)

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
        graph.commit_node(node, origin=CommitmentOrigin.TEST_FIXTURE)
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

        # 公式記録・オラクル（確定規則・規程）
        fb_authoritative = FeedbackResult(
            user_resolved=False,
            provenance=RelationProvenance(
                source_type="oracle",
                is_authoritative=True,
                channel="official_doc",
                claim_type="rule",
                target_relation="rule_promulgation",
            ),
        )
        self.assertEqual(compute_efp_prime_constraint(fb_authoritative), 1.0)

        # 管理者の是正命令（規程に基づく命令・決裁権限）
        fb_admin = FeedbackResult(
            user_resolved=False,
            correction_content="新制度条文第4条に基づく差し戻し",
            provenance=RelationProvenance(
                source_type="admin",
                authority_level="human_only",
                channel="admin_override",
                claim_type="rule",
                target_relation="approval_authority",
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
        graph.commit_node(node, origin=CommitmentOrigin.TEST_FIXTURE)
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
        graph.commit_node(node_hr, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(node_sec, origin=CommitmentOrigin.TEST_FIXTURE)

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
        graph.commit_node(node_a, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(node_b, origin=CommitmentOrigin.TEST_FIXTURE)

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
                claim_type="rule",
                target_relation="approval_authority",
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
        runtime.mb_graph.commit_node(node, origin=CommitmentOrigin.TEST_FIXTURE)

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
        runtime.mb_graph.commit_node(node, origin=CommitmentOrigin.TEST_FIXTURE)

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
                claim_type="rule",
                target_relation="approval_authority",
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
                claim_type="rule",
                target_relation="approval_authority",
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
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RelationConstraintLocator, ConstraintContext

        graph = MBGraph()
        n1 = MBNode(id="node_pay_1", domain="finance", trigger_pattern={"exact_keys": ["請求書支払"]}, action_template={"type": "direct_reply", "payload": "A"})
        n2 = MBNode(id="node_pay_2", domain="finance", trigger_pattern={"exact_keys": ["請求書支払", "振込"]}, action_template={"type": "direct_reply", "payload": "A"})
        n3 = MBNode(id="node_pay_3", domain="finance", trigger_pattern={"exact_keys": ["経費精算"]}, action_template={"type": "direct_reply", "payload": "C"})
        n4 = MBNode(id="node_hr_1", domain="hr", trigger_pattern={"exact_keys": ["有給休暇"]}, action_template={"type": "direct_reply", "payload": "D"})

        graph.commit_node(n1, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n2, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n3, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n4, origin=CommitmentOrigin.TEST_FIXTURE)

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
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RelationConstraintLocator, ConstraintContext

        graph = MBGraph()
        n_main = MBNode(id="n_tax", domain="finance", trigger_pattern={"exact_keys": ["法人税"]}, action_template={"type": "direct_reply", "payload": "税率回答"})
        n_unrelated = MBNode(id="n_lunch", domain="finance", trigger_pattern={"exact_keys": ["社食代補助"]}, action_template={"type": "direct_reply", "payload": "補助回答"})
        graph.commit_node(n_main, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n_unrelated, origin=CommitmentOrigin.TEST_FIXTURE)

        locator = RelationConstraintLocator()
        efp = _make_efp("法人税の申告")
        ctx = ConstraintContext(efp=efp, active_domain="finance")

        bundle = locator.locate_bundle_for_node(graph, n_main, ctx)
        self.assertEqual(bundle.primary_node_id(), "n_tax")
        # direct_reply が同じでも、キーを共有しない無関係ノードは束に含まれないこと
        self.assertNotIn("n_lunch", bundle.node_ids)

    def test_bundle_constraint_score_synergy(self):
        """支援ノード群による相乗効果（synergy boost）が束の総合拘束スコアを高めること"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RelationConstraintLocator, ConstraintContext

        # 孤立した単独ノードグラフ
        graph_solo = MBGraph()
        n_solo = MBNode(id="n_solo", domain="finance", trigger_pattern={"exact_keys": ["海外送金"]}, action_template={"type": "direct_reply", "payload": "A"}, confidence=0.7, approval_count=5)
        graph_solo.commit_node(n_solo, origin=CommitmentOrigin.TEST_FIXTURE)

        # 相互補強する支援ノードが存在するグラフ
        graph_bundle = MBGraph()
        n_base = MBNode(id="n_base", domain="finance", trigger_pattern={"exact_keys": ["海外送金"]}, action_template={"type": "direct_reply", "payload": "A"}, confidence=0.7, approval_count=5)
        n_supp = MBNode(id="n_supp", domain="finance", trigger_pattern={"exact_keys": ["海外送金", "SWIFTコード"]}, action_template={"type": "direct_reply", "payload": "A"}, confidence=0.7, approval_count=10)
        graph_bundle.commit_node(n_base, origin=CommitmentOrigin.TEST_FIXTURE)
        graph_bundle.commit_node(n_supp, origin=CommitmentOrigin.TEST_FIXTURE)

        locator = RelationConstraintLocator()
        efp = _make_efp("海外送金の手数料")
        ctx = ConstraintContext(efp=efp, active_domain="finance")

        bundle_solo = locator.locate_bundle_for_node(graph_solo, n_solo, ctx)
        bundle_multi = locator.locate_bundle_for_node(graph_bundle, n_base, ctx)

        # 支援ノードの裏付けがある束の総合拘束スコアは単独ノードより高いこと
        self.assertGreater(bundle_multi.constraint_score, bundle_solo.constraint_score)

    def test_mb_graph_key_index_hot_path_fast_lookup(self):
        """MBGraph の _key_index による高速共起検索が正しく機能すること"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin

        graph = MBGraph()
        n1 = MBNode(id="n1", domain="tech", trigger_pattern={"exact_keys": ["git pull", "git merge"]}, action_template={"type": "direct_reply", "payload": "A"})
        n2 = MBNode(id="n2", domain="tech", trigger_pattern={"exact_keys": ["git push", "git pull"]}, action_template={"type": "direct_reply", "payload": "B"})
        n3 = MBNode(id="n3", domain="tech", trigger_pattern={"exact_keys": ["docker run"]}, action_template={"type": "direct_reply", "payload": "C"})
        graph.commit_node(n1, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n2, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n3, origin=CommitmentOrigin.TEST_FIXTURE)

        related = graph.find_co_occurring_nodes(n1)
        related_ids = [r.id for r in related]
        self.assertIn("n2", related_ids)
        self.assertNotIn("n3", related_ids)
        self.assertNotIn("n1", related_ids)

    def test_bridge_node_detection_with_multi_node_bundles(self):
        """multi-node bundle が存在しても bridge ノードが重複生成されないこと"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RelationConstraintLocator, ConstraintContext

        graph = MBGraph()
        # bridge ノードと共起ノード
        n_bridge = MBNode(id="n_bridge", domain="special", trigger_pattern={"exact_keys": ["極秘事項A", "極秘事項B"]}, action_template={"type": "direct_reply", "payload": "A"})
        n_supp = MBNode(id="n_supp", domain="special", trigger_pattern={"exact_keys": ["極秘事項A"]}, action_template={"type": "direct_reply", "payload": "A"})
        graph.commit_node(n_bridge, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n_supp, origin=CommitmentOrigin.TEST_FIXTURE)

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
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RelationConstraintLocator, RuptureProbe, ConstraintContext

        graph = MBGraph()
        # 代表ノードは未承認 (approval_count = 0)
        n_primary = MBNode(id="n_new_flow", domain="sales", trigger_pattern={"exact_keys": ["新規見積作成"]}, action_template={"type": "direct_reply", "payload": "見積書フォーマット"}, confidence=0.6, approval_count=0)
        # 支援ノードは高承認 (approval_count = 10)
        n_supp = MBNode(id="n_old_flow", domain="sales", trigger_pattern={"exact_keys": ["新規見積作成", "割引率"]}, action_template={"type": "direct_reply", "payload": "見積書フォーマット"}, confidence=0.8, approval_count=10)
        graph.commit_node(n_primary, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n_supp, origin=CommitmentOrigin.TEST_FIXTURE)

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
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import ConstraintBundle, RuptureProbe, ConstraintContext

        graph = MBGraph()
        n1 = MBNode(id="n1", domain="sales", trigger_pattern={"exact_keys": ["割引"]}, action_template={"type": "direct_reply", "payload": "即時承認"}, confidence=0.8, approval_count=5)
        n2 = MBNode(id="n2", domain="sales", trigger_pattern={"exact_keys": ["割引"]}, action_template={"type": "direct_reply", "payload": "部長決裁必須"}, confidence=0.8, approval_count=5)
        graph.commit_node(n1, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n2, origin=CommitmentOrigin.TEST_FIXTURE)

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
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin

        graph = MBGraph()
        n_main = MBNode(id="n_main", domain="tech", trigger_pattern={"exact_keys": ["デプロイ"]}, action_template={"type": "direct_reply", "payload": "OK"})
        graph.commit_node(n_main, origin=CommitmentOrigin.TEST_FIXTURE)

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
            graph.commit_node(node, origin=CommitmentOrigin.TEST_FIXTURE)

        # 複数回呼び出して完全に同一のID順列が返ることを確認
        res1 = [n.id for n in graph.find_co_occurring_nodes(n_main, limit=4)]
        res2 = [n.id for n in graph.find_co_occurring_nodes(n_main, limit=4)]
        self.assertEqual(res1, res2)
        # 承認数降順・confidence降順により、上位は node_09, node_08, node_07, node_06 であること
        self.assertEqual(res1, ["node_09", "node_08", "node_07", "node_06"])

    def test_stale_or_rejected_support_node_cannot_boost_survive(self):
        """陳腐化または大量拒絶された支援ノードは健全性検査で除外され、未承認ノードを survive させないこと"""
        from datetime import datetime, timezone, timedelta
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
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
            last_support_at=now.isoformat(),
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
            last_support_at=now.isoformat(),
        )
        graph.commit_node(n_primary, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n_stale, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n_rejected, origin=CommitmentOrigin.TEST_FIXTURE)

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
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RelationConstraintLocator, ConstraintContext

        # グラフA: 同一マニュアルから複製されたノード群
        graph_dup = MBGraph()
        n_base_dup = MBNode(id="n_b1", domain="hr", trigger_pattern={"exact_keys": ["育休"]}, action_template={"type": "direct_reply", "payload": "A"}, confidence=0.7, approval_count=5, source_lineage="manual_hr_v1")
        n_s1_dup = MBNode(id="n_s1", domain="hr", trigger_pattern={"exact_keys": ["育休", "給付金"]}, action_template={"type": "direct_reply", "payload": "A"}, confidence=0.7, approval_count=5, source_lineage="manual_hr_v1")
        n_s2_dup = MBNode(id="n_s2", domain="hr", trigger_pattern={"exact_keys": ["育休", "申請書"]}, action_template={"type": "direct_reply", "payload": "A"}, confidence=0.7, approval_count=5, source_lineage="manual_hr_v1")
        graph_dup.commit_node(n_base_dup, origin=CommitmentOrigin.TEST_FIXTURE)
        graph_dup.commit_node(n_s1_dup, origin=CommitmentOrigin.TEST_FIXTURE)
        graph_dup.commit_node(n_s2_dup, origin=CommitmentOrigin.TEST_FIXTURE)

        # グラフB: 独立した関係源（法務決定、監査ログ）からのノード群
        graph_indep = MBGraph()
        n_base_ind = MBNode(id="n_b2", domain="hr", trigger_pattern={"exact_keys": ["育休"]}, action_template={"type": "direct_reply", "payload": "A"}, confidence=0.7, approval_count=5, source_lineage="manual_hr_v1")
        n_s1_ind = MBNode(id="n_s1_ind", domain="hr", trigger_pattern={"exact_keys": ["育休", "給付金"]}, action_template={"type": "direct_reply", "payload": "A"}, confidence=0.7, approval_count=5, source_lineage="audit_log_2026")
        n_s2_ind = MBNode(id="n_s2_ind", domain="hr", trigger_pattern={"exact_keys": ["育休", "申請書"]}, action_template={"type": "direct_reply", "payload": "A"}, confidence=0.7, approval_count=5, source_lineage="labor_law_amendment")
        graph_indep.commit_node(n_base_ind, origin=CommitmentOrigin.TEST_FIXTURE)
        graph_indep.commit_node(n_s1_ind, origin=CommitmentOrigin.TEST_FIXTURE)
        graph_indep.commit_node(n_s2_ind, origin=CommitmentOrigin.TEST_FIXTURE)

        locator = RelationConstraintLocator()
        efp = _make_efp("育休の申請手続き", category="hr")
        ctx = ConstraintContext(efp=efp, active_domain="hr")

        bundle_dup = locator.locate_bundle_for_node(graph_dup, n_base_dup, ctx)
        bundle_indep = locator.locate_bundle_for_node(graph_indep, n_base_ind, ctx)

        # 独立した系譜からの支持を持つ bundle_indep の方が総合拘束スコアが高いこと
        self.assertGreater(bundle_indep.constraint_score, bundle_dup.constraint_score)

    def test_actual_bundle_removal_perturbation(self):
        """実効的バンドル除去切断摂動 (End-to-End完全無加工): 束を切断したときに潜在対向ノードが露出すれば break と判定されること"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RelationConstraintLocator, RuptureProbe, ConstraintContext

        graph = MBGraph()
        # 既存ノード（代表ノード: 振込を通常回答）
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
        graph.commit_node(n_normal, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n_compliance, origin=CommitmentOrigin.TEST_FIXTURE)

        locator = RelationConstraintLocator()
        efp = _make_efp("送金振込", category="finance")
        ctx = ConstraintContext(efp=efp, active_domain="finance")

        # 完全無加工: Locator はアクションが対立する n_compliance を支援ノードから自然に除外する
        bundle = locator.locate_bundle_for_node(graph, n_normal, ctx)
        self.assertEqual(bundle.node_ids, ["n_normal"])

        probe = RuptureProbe()
        result = probe.probe(bundle, graph, ctx)

        # 実効的切断摂動により、潜在対向ノード n_compliance との衝突が露出し break すること
        self.assertEqual(result.verdict, "break")
        self.assertIn("実効的バンドル切断", result.rupture_reason)
        # 束切断による F の変化量 (rupture_effect) が記録されていること
        self.assertGreater(result.rupture_effect, 0.0)

    def test_rupture_effect_quantified_and_distinguished_from_break(self):
        """切ると変わる（拘束強度: rupture_effect）と切ると対向解釈が出る（競合: break）が直交して分離されること"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RelationConstraintLocator, RuptureProbe, ConstraintContext

        graph = MBGraph()
        # 唯一の承認済み基盤ノード
        n_pillar = MBNode(
            id="n_pillar",
            domain="hr",
            trigger_pattern={"exact_keys": ["福利厚生申請"]},
            action_template={"type": "direct_reply", "payload": "申請ポータルURL"},
            confidence=0.85,
            approval_count=10,
        )
        graph.commit_node(n_pillar, origin=CommitmentOrigin.TEST_FIXTURE)

        locator = RelationConstraintLocator()
        efp = _make_efp("福利厚生申請", category="hr")
        ctx = ConstraintContext(efp=efp, active_domain="hr")

        bundle = locator.locate_bundle_for_node(graph, n_pillar, ctx)
        probe = RuptureProbe()
        result = probe.probe(bundle, graph, ctx)

        # 切断によってフォールバック（ask_human）に縮退するため、変化量 rupture_effect は極めて高い（> 0.7）
        self.assertGreaterEqual(result.rupture_effect, 0.7)
        # しかし潜在対向解釈の露出ではないため break にはならず、健全な承認実績により survive すること
        self.assertEqual(result.verdict, "survive")

    def test_unhealthy_or_conflicting_nodes_excluded_from_bundle_ids(self):
        """陳腐化・大量拒絶・アクション対立ノードが bundle.node_ids に最初から混入しないこと"""
        from datetime import datetime, timezone, timedelta
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RelationConstraintLocator, ConstraintContext

        now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
        graph = MBGraph()
        n_prim = MBNode(id="n_prim", domain="it", trigger_pattern={"exact_keys": ["パスワードリセット"]}, action_template={"type": "direct_reply", "payload": "A"}, approval_count=5, last_updated=now.isoformat())
        # 陳腐化
        n_stale = MBNode(id="n_stale", domain="it", trigger_pattern={"exact_keys": ["パスワードリセット"]}, action_template={"type": "direct_reply", "payload": "A"}, approval_count=5, last_updated=(now - timedelta(days=400)).isoformat())
        # 大量拒絶
        n_rej = MBNode(id="n_rej", domain="it", trigger_pattern={"exact_keys": ["パスワードリセット"]}, action_template={"type": "direct_reply", "payload": "A"}, approval_count=5, rejection_count=20, last_support_at=now.isoformat())
        # アクション対立
        n_conflict = MBNode(id="n_conflict", domain="it", trigger_pattern={"exact_keys": ["パスワードリセット"]}, action_template={"type": "ask_human", "payload": "本人確認要"}, approval_count=5, last_updated=now.isoformat())
        # 健全な支援ノード
        n_healthy = MBNode(id="n_healthy", domain="it", trigger_pattern={"exact_keys": ["パスワードリセット", "SSO"]}, action_template={"type": "direct_reply", "payload": "A"}, approval_count=5, last_updated=now.isoformat())

        graph.commit_node(n_prim, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n_stale, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n_rej, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n_conflict, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n_healthy, origin=CommitmentOrigin.TEST_FIXTURE)

        locator = RelationConstraintLocator()
        efp = _make_efp("パスワードリセットのやり方", category="it")
        ctx = ConstraintContext(efp=efp, current_time=now, active_domain="it")

        bundle = locator.locate_bundle_for_node(graph, n_prim, ctx)
        # 束には代表ノードと健全な支援ノードのみが含まれること
        self.assertIn("n_prim", bundle.node_ids)
        self.assertIn("n_healthy", bundle.node_ids)
        self.assertNotIn("n_stale", bundle.node_ids)
        self.assertNotIn("n_rej", bundle.node_ids)
        self.assertNotIn("n_conflict", bundle.node_ids)

    def test_frozen_context_identical_cascade_used_in_probe(self):
        """RuptureProbe が ConstraintContext に渡された FrozenInterpretationContext の同一推論器を使用すること"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RelationConstraintLocator, RuptureProbe, ConstraintContext
        from rdl_enterprise.snapshot import FrozenInterpretationContext

        graph = MBGraph()
        n = MBNode(id="n1", domain="sales", trigger_pattern={"exact_keys": ["見積"]}, action_template={"type": "direct_reply", "payload": "見積回答"}, confidence=0.7, approval_count=5)
        graph.commit_node(n, origin=CommitmentOrigin.TEST_FIXTURE)

        # 初期キャッシュに特定のエントリを持つ凍結コンテキスト
        frozen_ctx = FrozenInterpretationContext(
            mb_version="v1.0",
            mb_content_hash=graph.content_hash(),
            frozen_mb=graph,
            target_domain="sales",
            initial_level0_cache={("v1.0", "sales", "見積"): "n1"},
        )

        efp = _make_efp("見積", category="sales")
        ctx = ConstraintContext(efp=efp, active_domain="sales", frozen_context=frozen_ctx)

        locator = RelationConstraintLocator()
        bundle = locator.locate_bundle_for_node(graph, n, ctx)

        probe = RuptureProbe()
        result = probe.probe(bundle, graph, ctx)
        # 凍結コンテキストから正常に評価され survive となること
        self.assertEqual(result.verdict, "survive")

    def test_admin_general_claim_and_target_relation_pure_relativization(self):
        """管理者や公式記録であっても general は 0.90、target_relation に応じて厳密に相対化されること"""
        from rdl_enterprise.snapshot import FeedbackResult, RelationProvenance
        from rdl_enterprise.constraint import compute_efp_prime_constraint

        # 1. 管理者 (admin) による一般言明 (claim_type="general") は 1.0 ではなく 0.90 に抑制
        prov_admin_gen = RelationProvenance(
            source_type="admin",
            is_authoritative=True,
            authority_level="human_only",
            channel="admin_override",
            claim_type="general",
        )
        fb_admin_gen = FeedbackResult(user_resolved=False, human_rejected=True, provenance=prov_admin_gen)
        self.assertAlmostEqual(compute_efp_prime_constraint(fb_admin_gen), 0.90, places=2)

        # 2. 管理者による事実報告 (target_relation="factual_report") は制度制定権ではないため 0.90 に抑制
        prov_admin_fact = RelationProvenance(
            source_type="admin",
            is_authoritative=True,
            authority_level="human_only",
            channel="admin_override",
            claim_type="fact",
            target_relation="factual_report",
        )
        fb_admin_fact = FeedbackResult(user_resolved=False, human_rejected=True, provenance=prov_admin_fact)
        self.assertAlmostEqual(compute_efp_prime_constraint(fb_admin_fact), 0.90, places=2)

        # 3. 管理者による一般照会 (target_relation="general_inquiry") は 0.85 に抑制
        prov_admin_inq = RelationProvenance(
            source_type="admin",
            is_authoritative=True,
            authority_level="human_only",
            channel="admin_override",
            claim_type="rule",
            target_relation="general_inquiry",
        )
        fb_admin_inq = FeedbackResult(user_resolved=False, human_rejected=True, provenance=prov_admin_inq)
        self.assertAlmostEqual(compute_efp_prime_constraint(fb_admin_inq), 0.85, places=2)

        # 4. 管理者による正式な制度改定 (claim_type="rule", target_relation="rule_promulgation") のみ満額 1.0
        prov_admin_rule = RelationProvenance(
            source_type="admin",
            is_authoritative=True,
            authority_level="human_only",
            channel="admin_override",
            claim_type="rule",
            target_relation="rule_promulgation",
        )
        fb_admin_rule = FeedbackResult(user_resolved=False, human_rejected=True, provenance=prov_admin_rule)
        self.assertAlmostEqual(compute_efp_prime_constraint(fb_admin_rule), 1.0, places=2)

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


    def test_isolated_dual_cascade_identical_c0_cut(self):
        """F_base と F_cut が独立した cascade インスタンスから同一 C0 で推論され、キャッシュ汚染が起きないこと"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RelationConstraintLocator, RuptureProbe, ConstraintContext
        from rdl_enterprise.snapshot import FrozenInterpretationContext

        graph = MBGraph()
        n = MBNode(id="n1", domain="sales", trigger_pattern={"exact_keys": ["見積作成"]}, action_template={"type": "direct_reply", "payload": "見積回答"}, confidence=0.7, approval_count=5)
        graph.commit_node(n, origin=CommitmentOrigin.TEST_FIXTURE)

        # 初期キャッシュ C0 を持つ凍結コンテキスト
        frozen_ctx = FrozenInterpretationContext(
            mb_version="v1.0",
            mb_content_hash=graph.content_hash(),
            frozen_mb=graph,
            target_domain="sales",
            initial_level0_cache={("v1.0", "sales", "見積作成"): "n1"},
        )

        efp = _make_efp("見積作成", category="sales")
        ctx = ConstraintContext(efp=efp, active_domain="sales", frozen_context=frozen_ctx)

        locator = RelationConstraintLocator()
        bundle = locator.locate_bundle_for_node(graph, n, ctx)

        probe = RuptureProbe()
        result = probe.probe(bundle, graph, ctx)

        # 独立した2つの Cascade により、切断時 (F_cut) に n1 が確実に除外され、フォールバック (ask_human) へ移行すること
        self.assertIsNotNone(result.rupture_effect)
        self.assertGreaterEqual(result.rupture_effect, 0.7)

    def test_rupture_effect_measured_across_all_verdicts(self):
        """鮮度低下や反証拒絶で break と判定される場合でも、rupture_effect が None や 0 ではなく実測されること"""
        from datetime import datetime, timezone, timedelta
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RelationConstraintLocator, RuptureProbe, ConstraintContext

        now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
        graph = MBGraph()
        # 365日放置されて鮮度が激減したノード
        n_stale = MBNode(
            id="n_stale_break",
            domain="ops",
            trigger_pattern={"exact_keys": ["デプロイ手順"]},
            action_template={"type": "direct_reply", "payload": "デプロイシェル実行"},
            confidence=0.8,
            approval_count=10,
            last_updated=(now - timedelta(days=365)).isoformat(),
        )
        graph.commit_node(n_stale, origin=CommitmentOrigin.TEST_FIXTURE)

        efp = _make_efp("デプロイ手順", category="ops")
        ctx = ConstraintContext(efp=efp, current_time=now, active_domain="ops")

        locator = RelationConstraintLocator()
        bundle = locator.locate_bundle_for_node(graph, n_stale, ctx)

        probe = RuptureProbe()
        result = probe.probe(bundle, graph, ctx)

        # 鮮度低下により verdict は break
        self.assertEqual(result.verdict, "break")
        self.assertIn("freshness 低下", result.rupture_reason)
        # しかし切断摂動による F 変化量は先行測定され、実測値 (Optional[float] として not None) が返ること！
        self.assertIsNotNone(result.rupture_effect)
        self.assertGreaterEqual(result.rupture_effect, 0.7)

    def test_unknown_claim_or_relation_capped_at_0_90(self):
        """admin や official_doc であっても claim_type や target_relation が未指定の場合は 1.0 に到達せず 0.90 に抑制されること"""
        from rdl_enterprise.snapshot import FeedbackResult, RelationProvenance
        from rdl_enterprise.constraint import compute_efp_prime_constraint

        # 1. claim_type=None, target_relation=None（権限内容が不明）
        prov_unknown = RelationProvenance(
            source_type="admin",
            authority_level="human_only",
            is_authoritative=True,
            channel="admin_override",
            claim_type=None,
            target_relation=None,
        )
        fb_unk = FeedbackResult(user_resolved=False, human_rejected=True, provenance=prov_unknown)
        # 未回収関係 ξ が大きいため 1.0 ではなく 0.90 に抑制されること
        self.assertAlmostEqual(compute_efp_prime_constraint(fb_unk), 0.90, places=2)

        # 2. claim_type="rule" だが target_relation=None（制度行使か現場意見か未明示）
        prov_no_rel = RelationProvenance(
            source_type="admin",
            authority_level="human_only",
            is_authoritative=True,
            channel="admin_override",
            claim_type="rule",
            target_relation=None,
        )
        fb_no_rel = FeedbackResult(user_resolved=False, human_rejected=True, provenance=prov_no_rel)
        self.assertAlmostEqual(compute_efp_prime_constraint(fb_no_rel), 0.90, places=2)

    def test_node_explicit_contradict_relation_and_payload_polarity_excluded_from_support(self):
        """node_relations による明示的 contradict および payload 極性矛盾（肯定 vs 否定）が支援束から除外されること"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RelationConstraintLocator, ConstraintContext

        graph = MBGraph()
        # 代表ノード: 申請可能
        n_main = MBNode(
            id="n_main",
            domain="hr",
            trigger_pattern={"exact_keys": ["特別休暇"]},
            action_template={"type": "direct_reply", "payload": "特別休暇の申請が可能です。"},
            approval_count=5,
            node_relations={"n_explicit_contradict": "contradict", "n_explicit_support": "support"},
        )
        # 支援候補A: 明示的 contradict 指定
        n_exp_contra = MBNode(
            id="n_explicit_contradict",
            domain="hr",
            trigger_pattern={"exact_keys": ["特別休暇"]},
            action_template={"type": "direct_reply", "payload": "何らかの回答"},
            approval_count=5,
        )
        # 支援候補B: 明示的 relation はないが、payload が「申請は禁止・不可」と明白な極性矛盾
        n_polarity_contra = MBNode(
            id="n_polarity_contra",
            domain="hr",
            trigger_pattern={"exact_keys": ["特別休暇"]},
            action_template={"type": "direct_reply", "payload": "当年度の特別休暇取得は禁止・不可とします。"},
            approval_count=5,
        )
        # 支援候補C: 明示的 support 指定
        n_exp_supp = MBNode(
            id="n_explicit_support",
            domain="hr",
            trigger_pattern={"exact_keys": ["特別休暇", "慶弔"]},
            action_template={"type": "direct_reply", "payload": "慶弔休暇申請が可能です。"},
            approval_count=5,
        )
        graph.commit_node(n_main, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n_exp_contra, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n_polarity_contra, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n_exp_supp, origin=CommitmentOrigin.TEST_FIXTURE)

        locator = RelationConstraintLocator()
        efp = _make_efp("特別休暇", category="hr")
        ctx = ConstraintContext(efp=efp, active_domain="hr")

        bundle = locator.locate_bundle_for_node(graph, n_main, ctx)

        # 支援束に n_main と n_explicit_support のみが含まれ、明示的 contradict や極性矛盾ノードが除外されていること
        self.assertIn("n_main", bundle.node_ids)
        self.assertIn("n_explicit_support", bundle.node_ids)
        self.assertNotIn("n_explicit_contradict", bundle.node_ids)
        self.assertNotIn("n_polarity_contra", bundle.node_ids)

    def test_level2_respects_skip_constraint_boost(self):
        """Level 2 推論時に skip_constraint_boost=True で _constraint_boost がスキップされること (再帰防止)"""
        from rdl_enterprise.cascade import InterpCascade, CascadeConfig
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.snapshot import BusinessInput

        graph = MBGraph()
        # Level 2 の bigram マッチとなるノード (完全一致キーではなく部分的一致)
        node = MBNode(
            id="n_l2",
            domain="finance",
            trigger_pattern={"exact_keys": ["定期代精算申請"]},
            action_template={"type": "direct_reply", "payload": "定期代の申請です"},
            confidence=0.5,
            approval_count=10,
        )
        graph.commit_node(node, origin=CommitmentOrigin.TEST_FIXTURE)

        cascade = InterpCascade(graph, config=CascadeConfig(level2_threshold=0.3))
        efp = _make_efp("定期代精算の手順を教えて", category="finance")

        # skip_constraint_boost=False (通常): boost が加算される
        pred_normal = cascade.interpret(efp, skip_constraint_boost=False)
        self.assertEqual(pred_normal.cost_tier, 2)

        # skip_constraint_boost=True: boost が加算されず 0.0
        pred_skip = cascade.interpret(efp, skip_constraint_boost=True)
        self.assertEqual(pred_skip.cost_tier, 2)
        # boost がスキップされたため confidence は通常時以下
        self.assertLessEqual(pred_skip.confidence, pred_normal.confidence)

    def test_explicit_unknown_relation_excluded_from_bundle(self):
        """明示的に unknown と指定されたノードが支援束から除外されること (B4/B5: ξとして保持)"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RelationConstraintLocator, ConstraintContext, _check_node_relation

        graph = MBGraph()
        n_main = MBNode(
            id="n_primary",
            domain="sales",
            trigger_pattern={"exact_keys": ["値引き申請"]},
            action_template={"type": "direct_reply", "payload": "値引き承認手順"},
            node_relations={"n_unknown_cand": "unknown"},
            approval_count=10,
        )
        n_unknown_cand = MBNode(
            id="n_unknown_cand",
            domain="sales",
            trigger_pattern={"exact_keys": ["値引き申請"]},
            action_template={"type": "direct_reply", "payload": "値引き承認手順"},
            approval_count=10,
        )
        graph.commit_node(n_main, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n_unknown_cand, origin=CommitmentOrigin.TEST_FIXTURE)

        # _check_node_relation が "unknown" を返すこと
        self.assertEqual(_check_node_relation(n_main, n_unknown_cand), "unknown")

        locator = RelationConstraintLocator()
        efp = _make_efp("値引き申請", category="sales")
        ctx = ConstraintContext(efp=efp, active_domain="sales")

        bundle = locator.locate_bundle_for_node(graph, n_main, ctx)
        self.assertEqual(bundle.primary_node_id(), "n_primary")
        self.assertNotIn("n_unknown_cand", bundle.node_ids)

    def test_level3_cut_requires_deterministic_replay(self):
        """Level 3 (LLM Bridge) において決定性保証がない場合は切断変化量を None (ξ) とすること"""
        from rdl_enterprise.cascade import InterpCascade, CascadeConfig
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RuptureProbe, ConstraintBundle, ConstraintContext
        from rdl_enterprise.snapshot import FrozenInterpretationContext

        class NonDeterministicMockBridge:
            def __init__(self):
                self.call_count = 0
            def resolve(self, efp):
                self.call_count += 1
                return {"type": f"reply_{self.call_count}", "payload": f"content_{self.call_count}"}

        class DeterministicMockBridge:
            def __init__(self):
                self.deterministic_replay = True
            def resolve(self, efp):
                return {"type": "direct_reply", "payload": "fixed_deterministic_response"}

        # 1. 非決定性 Bridge の場合: rupture_effect は None (測定不能)
        graph = MBGraph()
        # 束ノードを登録 (完全一致キー)
        n_cut = MBNode(id="n_cut_me", domain="hr", trigger_pattern={"exact_keys": ["特殊照会"]}, action_template={"type": "direct_reply", "payload": "A"}, approval_count=5)
        graph.commit_node(n_cut, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.freeze()

        bridge_non_det = NonDeterministicMockBridge()
        frozen_ctx_non_det = FrozenInterpretationContext(
            mb_version="v1",
            mb_content_hash="hash1",
            frozen_mb=graph,
            llm_bridge=bridge_non_det,
            target_domain="hr",
        )
        efp = _make_efp("特殊照会", category="hr")
        ctx_non_det = ConstraintContext(efp=efp, active_domain="hr", frozen_context=frozen_ctx_non_det)

        bundle = ConstraintBundle(node_ids=["n_cut_me"], locus_type="strong", constraint_score=0.8, freshness=0.9, relevance=0.8)
        probe = RuptureProbe()

        res_non_det = probe.probe(bundle, graph, ctx_non_det)
        # 非決定性 bridge により Level 3 予測の再現性が保証されないため None
        self.assertIsNone(res_non_det.rupture_effect)

        # 2. 決定性 Bridge (deterministic_replay=True) の場合: rupture_effect が実測される
        bridge_det = DeterministicMockBridge()
        frozen_ctx_det = FrozenInterpretationContext(
            mb_version="v1",
            mb_content_hash="hash1",
            frozen_mb=graph,
            llm_bridge=bridge_det,
            target_domain="hr",
        )
        ctx_det = ConstraintContext(efp=efp, active_domain="hr", frozen_context=frozen_ctx_det)
        res_det = probe.probe(bundle, graph, ctx_det)
        self.assertIsNotNone(res_det.rupture_effect)
        self.assertGreater(res_det.rupture_effect, 0.0)

    def test_rupture_probe_respects_explicit_support_despite_payload_diff(self):
        """明示的 support があれば payload 文字列が異なっていても内部亀裂にならず survive 判定へ進むこと"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RelationConstraintLocator, RuptureProbe, ConstraintContext

        graph = MBGraph()
        # 代表ノード: 「特別休暇は申請可能」
        n_a = MBNode(
            id="n_a",
            domain="hr",
            trigger_pattern={"exact_keys": ["特別休暇"]},
            action_template={"type": "direct_reply", "payload": "特別休暇は申請可能です。"},
            node_relations={"n_b": "support"},
            confidence=0.8,
            approval_count=10,
        )
        # 支援ノード: 「慶弔の場合は特別休暇として申請可能」（payload 文字列は異なるが明示的 support）
        n_b = MBNode(
            id="n_b",
            domain="hr",
            trigger_pattern={"exact_keys": ["特別休暇", "慶弔"]},
            action_template={"type": "direct_reply", "payload": "慶弔の場合は特別休暇として申請可能です。"},
            node_relations={"n_a": "support"},
            confidence=0.8,
            approval_count=10,
        )
        graph.commit_node(n_a, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n_b, origin=CommitmentOrigin.TEST_FIXTURE)

        locator = RelationConstraintLocator()
        efp = _make_efp("特別休暇の申請", category="hr")
        ctx = ConstraintContext(efp=efp, active_domain="hr")

        bundle = locator.locate_bundle_for_node(graph, n_a, ctx)
        self.assertIn("n_b", bundle.node_ids)

        probe = RuptureProbe()
        result = probe.probe(bundle, graph, ctx)
        # 旧式の payload 完全一致比較なら unresolved になっていたが、明示的 support により survive すること
        self.assertEqual(result.verdict, "survive")

    def test_inferred_support_has_reduced_synergy_and_cannot_solely_survive(self):
        """未指定で payload が異なる inferred_support は synergy が抑制され、単独で survive の根拠にならないこと"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RelationConstraintLocator, RuptureProbe, ConstraintContext

        graph = MBGraph()
        # 代表ノード (approval_count = 0)
        n_main = MBNode(
            id="n_main",
            domain="finance",
            trigger_pattern={"exact_keys": ["経費"]},
            action_template={"type": "direct_reply", "payload": "経費精算ガイド"},
            approval_count=0,
        )
        # 支援ノード (approval_count = 10, payload は異なるが action_type 同一・未指定 relation)
        n_inferred = MBNode(
            id="n_inferred",
            domain="finance",
            trigger_pattern={"exact_keys": ["経費", "交通費"]},
            action_template={"type": "direct_reply", "payload": "交通費精算ガイド"},
            approval_count=10,
        )
        graph.commit_node(n_main, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n_inferred, origin=CommitmentOrigin.TEST_FIXTURE)

        locator = RelationConstraintLocator()
        efp = _make_efp("経費申請", category="finance")
        ctx = ConstraintContext(efp=efp, active_domain="finance")

        bundle = locator.locate_bundle_for_node(graph, n_main, ctx)
        # inferred_support は bundle.node_ids (確定束) ではなく bundle.inferred_node_ids (推論束) に分離されること
        self.assertNotIn("n_inferred", bundle.node_ids)
        self.assertIn("n_inferred", bundle.inferred_node_ids)

        probe = RuptureProbe()
        result = probe.probe(bundle, graph, ctx)
        # 代表ノードが未承認 (0) であり、inferred_support は単独で survive を支えないため unresolved に留まること
        self.assertEqual(result.verdict, "unresolved")

    def test_llm_bridge_identity_includes_replay_snapshot_hash(self):
        """LLMBridgeIdentity に seed, deterministic_replay, replay_snapshot_hash が包含され、Probe の決定性監査に効くこと"""
        from rdl_enterprise.snapshot import LLMBridgeIdentity, FrozenInterpretationContext
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RuptureProbe, ConstraintBundle, ConstraintContext

        class SnapshotReplayBridge:
            def __init__(self, snapshot_hash="snap_abc123"):
                self.model_name = "claude-3-5"
                self.replay_snapshot_hash = snapshot_hash
                self.seed = 42
                self.temperature = 0.0
            def resolve(self, efp):
                return {"type": "direct_reply", "payload": "snapshot_replayed_content"}

        bridge = SnapshotReplayBridge()
        identity = LLMBridgeIdentity.from_bridge(bridge)

        bridge2 = SnapshotReplayBridge(snapshot_hash="snap_xyz789")
        identity2 = LLMBridgeIdentity.from_bridge(bridge2)
        self.assertEqual(identity.seed, 42)
        self.assertEqual(identity.replay_snapshot_hash, "snap_abc123")
        self.assertNotEqual(identity.config_hash, identity2.config_hash)

        # 凍結コンテキストのハッシュ検証
        graph = MBGraph()
        n = MBNode(id="n1", domain="it", trigger_pattern={"exact_keys": ["PC手配"]}, action_template={"type": "direct_reply", "payload": "手配手順"})
        graph.commit_node(n, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.freeze()

        frozen_ctx = FrozenInterpretationContext(
            mb_version="v1",
            mb_content_hash="hash1",
            frozen_mb=graph,
            llm_bridge=bridge,
            target_domain="it",
        )
        self.assertIsNotNone(frozen_ctx.context_hash)

        # replay_snapshot_hash が有効な bridge は決定性ありとみなされ、rupture_effect が実測されること
        efp = _make_efp("PC手配", category="it")
        ctx = ConstraintContext(efp=efp, active_domain="it", frozen_context=frozen_ctx)
        bundle = ConstraintBundle(node_ids=["n1"], locus_type="strong", constraint_score=0.8, freshness=0.9, relevance=0.8)
        probe = RuptureProbe()
        res = probe.probe(bundle, graph, ctx)
        self.assertIsNotNone(res.rupture_effect)

    def test_inferred_support_separated_in_bundle_does_not_break_healthy_bundle(self):
        """健全な代表ノードがある場合、inferred_support が存在しても内部亀裂で道連れにならず survive すること"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RelationConstraintLocator, RuptureProbe, ConstraintContext

        graph = MBGraph()
        # 代表ノード: 健全 (approval_count = 15)
        n_main = MBNode(
            id="n_main_healthy",
            domain="finance",
            trigger_pattern={"exact_keys": ["請求書支払"]},
            action_template={"type": "direct_reply", "payload": "請求書支払の手順です"},
            approval_count=15,
        )
        # 支援候補: 未指定だが同一 action_type (payload 違い -> inferred_support)
        n_aux = MBNode(
            id="n_aux_inferred",
            domain="finance",
            trigger_pattern={"exact_keys": ["請求書支払", "振込"]},
            action_template={"type": "direct_reply", "payload": "振込支払の手順です"},
            approval_count=5,
        )
        graph.commit_node(n_main, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n_aux, origin=CommitmentOrigin.TEST_FIXTURE)

        locator = RelationConstraintLocator()
        efp = _make_efp("請求書支払", category="finance")
        ctx = ConstraintContext(efp=efp, active_domain="finance")

        bundle = locator.locate_bundle_for_node(graph, n_main, ctx)
        # 確定束 (node_ids) には n_main のみ、推論束 (inferred_node_ids) に n_aux が分離されていること
        self.assertEqual(bundle.node_ids, ["n_main_healthy"])
        self.assertEqual(bundle.inferred_node_ids, ["n_aux_inferred"])

        probe = RuptureProbe()
        result = probe.probe(bundle, graph, ctx)
        # inferred_support の存在によって内部亀裂にならず、代表ノードの実績により正常に survive すること
        self.assertEqual(result.verdict, "survive")

    def test_level3_rejects_plain_seed_without_replay_contract(self):
        """単なる seed/temperature=0 のみで can_replay も snapshot もない推論器は、決定性保証なしとして rupture_effect=None とすること"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RuptureProbe, ConstraintBundle, ConstraintContext
        from rdl_enterprise.snapshot import FrozenInterpretationContext

        class PlainSeedBridge:
            def __init__(self):
                self.model_name = "gpt-4"
                self.seed = 12345
                self.temperature = 0.0
                # can_replay なし、replay_snapshot_hash なし、deterministic_replay なし
            def resolve(self, efp):
                return {"type": "direct_reply", "payload": "plain_seed_reply"}

        graph = MBGraph()
        n = MBNode(id="n1", domain="cs", trigger_pattern={"exact_keys": ["契約解除"]}, action_template={"type": "direct_reply", "payload": "解約手順"})
        graph.commit_node(n, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.freeze()

        bridge = PlainSeedBridge()
        frozen_ctx = FrozenInterpretationContext(
            mb_version="v1",
            mb_content_hash="hash1",
            frozen_mb=graph,
            llm_bridge=bridge,
            target_domain="cs",
        )
        efp = _make_efp("契約解除の特例", category="cs")
        ctx = ConstraintContext(efp=efp, active_domain="cs", frozen_context=frozen_ctx)
        bundle = ConstraintBundle(node_ids=["n1"], locus_type="strong", constraint_score=0.8, freshness=0.9, relevance=0.8)
        probe = RuptureProbe()
        res = probe.probe(bundle, graph, ctx)
        # リプレイ能力の契約がないため None (ξ) として安全側に倒す
        self.assertIsNone(res.rupture_effect)

    def test_level3_accepts_can_replay_contract(self):
        """can_replay() -> True メソッド契約を持つ推論器は決定性ありと認定され、rupture_effect が実測されること"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RuptureProbe, ConstraintBundle, ConstraintContext
        from rdl_enterprise.snapshot import FrozenInterpretationContext

        class ReplayableBridge:
            def __init__(self):
                self.model_name = "custom-llm"
            def can_replay(self):
                return True
            def resolve(self, efp):
                return {"type": "direct_reply", "payload": "replayed_contract_reply"}

        graph = MBGraph()
        n = MBNode(id="n1", domain="cs", trigger_pattern={"exact_keys": ["契約解除"]}, action_template={"type": "direct_reply", "payload": "解約手順"})
        graph.commit_node(n, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.freeze()

        bridge = ReplayableBridge()
        frozen_ctx = FrozenInterpretationContext(
            mb_version="v1",
            mb_content_hash="hash1",
            frozen_mb=graph,
            llm_bridge=bridge,
            target_domain="cs",
        )
        efp = _make_efp("契約解除の特例", category="cs")
        ctx = ConstraintContext(efp=efp, active_domain="cs", frozen_context=frozen_ctx)
        bundle = ConstraintBundle(node_ids=["n1"], locus_type="strong", constraint_score=0.8, freshness=0.9, relevance=0.8)
        probe = RuptureProbe()
        res = probe.probe(bundle, graph, ctx)
        self.assertIsNotNone(res.rupture_effect)

    def test_level3_rejects_drifting_bridge_despite_can_replay_true(self):
        """can_replay()=True と公言しながら出力が揺らぐ bridge は、実際の Replay 不一致により rupture_effect=None となること"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RuptureProbe, ConstraintBundle, ConstraintContext
        from rdl_enterprise.snapshot import FrozenInterpretationContext

        class DriftingReplayBridge:
            def __init__(self):
                self.call_count = 0
            def can_replay(self):
                return True
            def resolve(self, efp):
                self.call_count += 1
                # 呼び出しごとに異なる結果を返す (口先だけの can_replay)
                return {"type": "direct_reply", "payload": f"drifting_payload_{self.call_count}"}

        graph = MBGraph()
        # 束ノード (切断対象外の未知クエリで Level 3 へフォールスルー)
        n = MBNode(id="n_other", domain="legal", trigger_pattern={"exact_keys": ["既知"]}, action_template={"type": "direct_reply", "payload": "既知回答"})
        graph.commit_node(n, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.freeze()

        bridge = DriftingReplayBridge()
        frozen_ctx = FrozenInterpretationContext(
            mb_version="v1",
            mb_content_hash="hash1",
            frozen_mb=graph,
            llm_bridge=bridge,
            target_domain="legal",
        )
        efp = _make_efp("完全未知の問い合わせ（Level 3到達）", category="legal")
        ctx = ConstraintContext(efp=efp, active_domain="legal", frozen_context=frozen_ctx)
        bundle = ConstraintBundle(node_ids=["n_other"], locus_type="strong", constraint_score=0.8, freshness=0.9, relevance=0.8)

        probe = RuptureProbe()
        res = probe.probe(bundle, graph, ctx)
        # f_base と f_without の両方が Level 3 に到達した際、出力が不一致（揺らぎ検知）のため None
        self.assertIsNone(res.rupture_effect)

    def test_level3_uses_resolve_replay_when_available(self):
        """bridge が resolve_replay() を提供する場合、カスケードおよび Probe で優先実行されて決定性が実証されること"""
        from rdl_enterprise.cascade import InterpCascade, CascadeConfig
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RuptureProbe, ConstraintBundle, ConstraintContext
        from rdl_enterprise.snapshot import FrozenInterpretationContext

        class DedicatedReplayBridge:
            def __init__(self):
                self.resolve_called = False
                self.resolve_replay_called = False
                self.received_replay_token = None
            def can_replay(self):
                return True
            def resolve(self, efp):
                self.resolve_called = True
                return {"type": "direct_reply", "payload": "normal_resolve"}
            def resolve_replay(self, efp, replay_token=None):
                self.resolve_replay_called = True
                self.received_replay_token = replay_token
                return {"type": "direct_reply", "payload": "exact_replay_output"}

        graph = MBGraph()
        bridge = DedicatedReplayBridge()
        cascade = InterpCascade(graph, llm_bridge=bridge)
        efp = _make_efp("未知クエリ", category="general")

        # 1. 通常推論: 常に resolve() が呼ばれ、resolve_replay は呼ばれない (逆転現象の解消)
        pred = cascade.interpret(efp)
        self.assertTrue(bridge.resolve_called)
        self.assertFalse(bridge.resolve_replay_called)
        self.assertEqual(pred.content, "normal_resolve")

        # 2. 破断実験 (RuptureProbe): Counterfactual Replay Contract により resolve_replay が呼ばれる
        node_x = MBNode(
            id="n_x",
            domain="general",
            trigger_pattern={"exact_keys": ["テストキー"]},
            action_template={"type": "direct_reply", "payload": "x"},
            confidence=0.8,
            approval_count=10,
        )
        graph.commit_node(node_x, origin=CommitmentOrigin.TEST_FIXTURE)
        bundle = ConstraintBundle(
            node_ids=["n_x"],
            locus_type="strong",
            constraint_score=0.8,
        )
        probe = RuptureProbe()
        ctx = ConstraintContext(efp=efp, current_time=datetime.utcnow(), llm_bridge=bridge)
        res = probe.probe(bundle, graph, ctx)
        self.assertTrue(bridge.resolve_replay_called)
        self.assertIsNotNone(bridge.received_replay_token)

    def test_level1_regex_rule_case_insensitive(self):
        """Level 1 の正規表現ルールが大文字小文字を区別せずマッチすること (P0 回帰テスト)"""
        from rdl_enterprise.cascade import InterpCascade
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin

        graph = MBGraph()
        node = MBNode(
            id="n_rule_case",
            domain="support",
            trigger_pattern={"rule_expr": r"^error:\s*\d+"},
            action_template={"type": "direct_reply", "payload": "エラーコード対応"},
            confidence=0.85,
        )
        graph.commit_node(node, origin=CommitmentOrigin.TEST_FIXTURE)
        cascade = InterpCascade(graph)

        # 大文字混在クエリ
        efp = _make_efp("ERROR: 404 Not Found", category="support")
        pred = cascade.interpret(efp)
        self.assertEqual(pred.matched_node_id, "n_rule_case")
        self.assertEqual(pred.cost_tier, 1)

    def test_level3_counterfactual_replay_mb_dependent_measures_diff(self):
        """is_mb_dependent な bridge では同一外生条件 K の下で内生的変化が rupture_effect として測定されること"""
        from rdl_enterprise.cascade import InterpCascade
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RuptureProbe, ConstraintBundle, ConstraintContext
        from rdl_enterprise.snapshot import ReplayToken

        class MBDependentBridge:
            def __init__(self):
                self.is_mb_dependent = True
                self.call_count = 0
            def can_replay(self):
                return True
            def resolve(self, efp):
                return {"type": "direct_reply", "payload": "base"}
            def resolve_replay(self, efp, replay_token=None, counterfactual_input=None):
                self.call_count += 1
                v_hash = counterfactual_input.mb_view.view_hash if (counterfactual_input and counterfactual_input.mb_view) else "v_hash"
                # 1回目 (f_base) と 2回目 (f_without) で内生的な差分を模倣
                if self.call_count == 1:
                    return {"type": "direct_reply", "payload": "base_with_mb", "applied_mb_view_hash": v_hash}
                return {"type": "direct_reply", "payload": "cut_without_mb", "applied_mb_view_hash": v_hash}

        graph = MBGraph()
        bridge = MBDependentBridge()
        cascade = InterpCascade(graph, llm_bridge=bridge)
        node = MBNode(
            id="n_test",
            domain="general",
            trigger_pattern={"exact_keys": ["テストキー"]},
            action_template={"type": "direct_reply", "payload": "x"},
            confidence=0.8,
            approval_count=10,
        )
        graph.commit_node(node, origin=CommitmentOrigin.TEST_FIXTURE)

        bundle = ConstraintBundle(node_ids=["n_test"], locus_type="strong", constraint_score=0.8)
        probe = RuptureProbe()
        efp = _make_efp("未知クエリ", category="general")
        ctx = ConstraintContext(efp=efp, current_time=datetime.utcnow(), llm_bridge=bridge)

        res = probe.probe(bundle, graph, ctx)
        # 内生変化（payload違い: 0.4）が rupture_effect として測定される
        self.assertIsNotNone(res.rupture_effect)
        self.assertAlmostEqual(res.rupture_effect, 0.4, places=2)

    def test_constraint_bundle_two_tier_core_and_auxiliary(self):
        """ConstraintBundle の core と auxiliary が分離され、convergence も独立していること"""
        from rdl_enterprise.constraint import (
            ConstraintBundle,
            RelationConstraintLocator,
            ConstraintContext,
        )
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin

        graph = MBGraph()
        n_main = MBNode(
            id="n1",
            domain="support",
            trigger_pattern={"exact_keys": ["テスト"]},
            action_template={"type": "direct_reply", "payload": "手順1"},
            confidence=0.7,
            approval_count=10,
        )
        n_inferred = MBNode(
            id="n2",
            domain="support",
            trigger_pattern={"exact_keys": ["テスト", "補助"]},
            action_template={"type": "direct_reply", "payload": "手順2"},
            confidence=0.8,
            approval_count=10,
        )
        graph.commit_node(n_main, origin=CommitmentOrigin.TEST_FIXTURE)

        locator = RelationConstraintLocator()
        efp = _make_efp("テスト", category="support")
        ctx = ConstraintContext(efp=efp, active_domain="support")

        # 1. 単一ノード時
        b1 = locator.locate_bundle_for_node(graph, n_main, ctx)
        self.assertIsNotNone(b1.core)
        self.assertIsNotNone(b1.auxiliary)
        base_core_score = b1.core.constraint_score
        base_core_conv = b1.core.convergence
        self.assertEqual(b1.auxiliary.constraint_signal, 0.0)
        self.assertEqual(b1.auxiliary.convergence_signal, 0.0)

        # 2. 推論ノード追加時
        graph.commit_node(n_inferred, origin=CommitmentOrigin.TEST_FIXTURE)
        b2 = locator.locate_bundle_for_node(graph, n_main, ctx)

        # core のスコアと収束度は全く変わらない
        self.assertEqual(b2.core.constraint_score, base_core_score)
        self.assertEqual(b2.core.convergence, base_core_conv)
        self.assertEqual(b2.core_constraint_score, base_core_score)
        self.assertEqual(b2.core_convergence, base_core_conv)

        # auxiliary (ξ) のシグナルのみが立ち上がる
        self.assertGreater(b2.auxiliary.constraint_signal, 0.0)
        self.assertGreater(b2.auxiliary.convergence_signal, 0.0)
        self.assertGreater(b2.auxiliary_constraint_signal, 0.0)
        self.assertGreater(b2.auxiliary_convergence_signal, 0.0)

    def test_auxiliary_constraint_signal_separated_from_core_constraint_score(self):
        """inferred_support は core_constraint_score に加算されず auxiliary_constraint_signal に隔離されること"""
        from rdl_enterprise.cascade import InterpCascade, CascadeConfig
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RelationConstraintLocator, ConstraintContext, ConstraintConfig

        graph = MBGraph()
        # 代表ノード: 承認数十分 (survive 対象)
        n_main = MBNode(
            id="n_main",
            domain="support",
            trigger_pattern={"exact_keys": ["パスワードリセット"]},
            action_template={"type": "direct_reply", "payload": "リセット手順"},
            confidence=0.7,
            approval_count=10,
        )
        # 支援候補: 未指定だが同一 action_type (payload 違い -> inferred_support)
        n_inferred = MBNode(
            id="n_inferred",
            domain="support",
            trigger_pattern={"exact_keys": ["パスワードリセット", "アカウントロック"]},
            action_template={"type": "direct_reply", "payload": "ロック解除手順"},
            confidence=0.8,
            approval_count=10,
        )
        graph.commit_node(n_main, origin=CommitmentOrigin.TEST_FIXTURE)

        locator = RelationConstraintLocator()
        efp = _make_efp("パスワードリセット", category="support")
        ctx = ConstraintContext(efp=efp, active_domain="support")

        # 1. 支援候補がない状態での代表ノード束
        bundle_single = locator.locate_bundle_for_node(graph, n_main, ctx)
        self.assertEqual(bundle_single.auxiliary_constraint_signal, 0.0)
        single_core_score = bundle_single.constraint_score

        # 2. inferred_support ノードを追加
        graph.commit_node(n_inferred, origin=CommitmentOrigin.TEST_FIXTURE)
        bundle_with_inferred = locator.locate_bundle_for_node(graph, n_main, ctx)

        # core_constraint_score には一切加算されないこと（確定拘束強度は単体時と不変）
        self.assertEqual(bundle_with_inferred.constraint_score, single_core_score)
        self.assertEqual(bundle_with_inferred.core_constraint_score, single_core_score)

        # auxiliary_constraint_signal にのみ潜在シグナルが分離記録されていること
        self.assertGreater(bundle_with_inferred.auxiliary_constraint_signal, 0.0)
        self.assertIn("n_inferred", bundle_with_inferred.inferred_node_ids)

        # 3. Cascade の推論ブーストでも auxiliary_constraint_signal は加算されないこと
        cfg = CascadeConfig()
        c_config = ConstraintConfig(constraint_boost_cap=0.2)
        cascade = InterpCascade(graph, config=cfg, constraint_config=c_config)
        pred = cascade.interpret(efp)
        # ブーストは core_constraint_score のみから算出されるため、inferred の有無でブースト量が増殖しない
        self.assertAlmostEqual(pred.confidence, min(1.0, n_main.confidence + single_core_score * 0.2), places=4)

    def test_level3_counterfactual_input_transfers_mb_intervention(self):
        """Level 3 counterfactual 再演時に CounterfactualInput (M_B と M_B \\ bundle の差異) が明示伝達されること"""
        from rdl_enterprise.cascade import InterpCascade
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RuptureProbe, ConstraintBundle, ConstraintContext
        from rdl_enterprise.snapshot import ReplayToken, CounterfactualInput

        received_cf_inputs = []

        class MBInterventionBridge:
            def __init__(self):
                self.can_replay_flag = True
            def can_replay(self):
                return True
            def resolve(self, efp):
                return {"type": "direct_reply", "payload": "base_output"}
            def resolve_counterfactual(self, efp, replay_token, counterfactual_input=None):
                received_cf_inputs.append(counterfactual_input)
                v_hash = counterfactual_input.mb_view.view_hash if (counterfactual_input and counterfactual_input.mb_view) else "v_hash"
                # available_nodes の有無によってプロンプト/出力を実際に変化させる (内生的変化)
                if counterfactual_input and len(counterfactual_input.available_nodes) > 0:
                    return {"type": "direct_reply", "payload": f"with_nodes_{len(counterfactual_input.available_nodes)}", "applied_mb_view_hash": v_hash}
                return {"type": "direct_reply", "payload": "cut_without_nodes", "applied_mb_view_hash": v_hash}

        graph = MBGraph()
        n1 = MBNode(
            id="n1",
            domain="general",
            trigger_pattern={"exact_keys": ["キーワード1特例措置"]},
            action_template={"type": "direct_reply", "payload": "x"},
            confidence=0.8,
            approval_count=10,
        )
        graph.commit_node(n1, origin=CommitmentOrigin.TEST_FIXTURE)

        bridge = MBInterventionBridge()
        bundle = ConstraintBundle(node_ids=["n1"], locus_type="strong", constraint_score=0.8)
        probe = RuptureProbe()
        efp = _make_efp("キーワード1の申請窓口について教えてください", category="general")
        ctx = ConstraintContext(efp=efp, current_time=datetime.utcnow(), llm_bridge=bridge)

        res = probe.probe(bundle, graph, ctx)

        # 2回の counterfactual 呼び出し (f_base と f_without) で CounterfactualInput が渡されたこと
        self.assertEqual(len(received_cf_inputs), 2)
        cf_base, cf_cut = received_cf_inputs[0], received_cf_inputs[1]

        # f_base: 除外ノードなし、n1 が利用可能
        self.assertEqual(cf_base.excluded_node_ids, [])
        self.assertEqual(len(cf_base.available_nodes), 1)
        self.assertEqual(cf_base.available_nodes[0].id, "n1")

        # f_cut: n1 が除外ノード、利用可能ノードは 0
        self.assertEqual(cf_cut.excluded_node_ids, ["n1"])
        self.assertEqual(len(cf_cut.available_nodes), 0)

        # M_B 切断という唯一の内生的介入変数により出力差分 (0.4) が正しく rupture_effect として測定されたこと
        self.assertIsNotNone(res.rupture_effect)
        self.assertAlmostEqual(res.rupture_effect, 0.4, places=2)

    def test_resolve_emits_actual_replay_token_and_probe_reuses_it(self):
        """resolve() 自体から排出された actual ReplayToken が prediction に封入され RuptureProbe で再利用されること"""
        from rdl_enterprise.cascade import InterpCascade
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RuptureProbe, ConstraintBundle, ConstraintContext
        from rdl_enterprise.snapshot import ReplayToken

        actual_token = ReplayToken(
            token_id="tok_actual_123",
            model_name="test-model",
            seed=999,
        )

        class TraceEmittingBridge:
            def __init__(self):
                self.received_replay_tokens = []
            def can_replay(self):
                return True
            def resolve(self, efp):
                # 作用の実行証跡として actual_token を排出
                return {
                    "type": "direct_reply",
                    "payload": "trace_output",
                    "replay_token": actual_token,
                }
            def resolve_counterfactual(self, efp, replay_token, counterfactual_input=None):
                self.received_replay_tokens.append(replay_token)
                return {"type": "direct_reply", "payload": "trace_output"}

        graph = MBGraph()
        bridge = TraceEmittingBridge()
        cascade = InterpCascade(graph, llm_bridge=bridge)
        efp = _make_efp("未知クエリ", category="general")

        # 1. 通常推論で actual_token が prediction.replay_token に格納されること
        pred = cascade.interpret(efp)
        self.assertEqual(pred.replay_token, actual_token)
        self.assertEqual(pred.replay_token.token_id, "tok_actual_123")

        # 2. その actual_token を固定した ConstraintContext で RuptureProbe を実行
        node_x = MBNode(
            id="n_x",
            domain="general",
            trigger_pattern={"exact_keys": ["テストキー"]},
            action_template={"type": "direct_reply", "payload": "x"},
            confidence=0.8,
            approval_count=10,
        )
        graph.commit_node(node_x, origin=CommitmentOrigin.TEST_FIXTURE)
        bundle = ConstraintBundle(node_ids=["n_x"], locus_type="strong", constraint_score=0.8)
        probe = RuptureProbe()

        ctx = ConstraintContext(
            efp=efp,
            current_time=datetime.utcnow(),
            llm_bridge=bridge,
            actual_replay_token=pred.replay_token,  # F を生んだ証跡をそのまま固定
        )
        res = probe.probe(bundle, graph, ctx)

        # プローブ内の反実仮想再演で、実際に pred.replay_token (actual_token) が再利用されたこと
        self.assertGreaterEqual(len(bridge.received_replay_tokens), 1)
        self.assertEqual(bridge.received_replay_tokens[0].token_id, "tok_actual_123")

    def test_replay_token_separates_token_id_and_conditions_hash(self):
        """token_id が異なっていても外生条件 K が同一なら conditions_hash が一致すること"""
        from rdl_enterprise.snapshot import ReplayToken

        token_a = ReplayToken(
            token_id="tok_uuid_111",
            model_name="claude-3-5-sonnet",
            seed=42,
            sampling_params={"temperature": 0.0},
        )
        token_b = ReplayToken(
            token_id="tok_uuid_222",
            model_name="claude-3-5-sonnet",
            seed=42,
            sampling_params={"temperature": 0.0},
        )
        token_c = ReplayToken(
            token_id="tok_uuid_111",
            model_name="claude-3-5-sonnet",
            seed=43,  # 異なる seed (異なる外生条件)
            sampling_params={"temperature": 0.0},
        )

        # token_id は異なる
        self.assertNotEqual(token_a.token_id, token_b.token_id)
        # 外生条件集合 K の意味的ハッシュは完全に一致する
        self.assertEqual(token_a.conditions_hash, token_b.conditions_hash)
        self.assertEqual(token_a.compute_hash(), token_b.compute_hash())

        # 条件が異なればハッシュも異なる
        self.assertNotEqual(token_a.conditions_hash, token_c.conditions_hash)

    def test_constraint_bundle_canonical_storage_delegates_completely(self):
        """ConstraintBundle の canonical storage が core と auxiliary に一本化され乖離不能であること"""
        from rdl_enterprise.constraint import ConstraintBundle, BundleCore, BundleAuxiliary

        bundle = ConstraintBundle(
            node_ids=["n_main"],
            constraint_score=0.75,
            convergence=0.6,
            inferred_node_ids=["n_inf"],
            auxiliary_constraint_signal=0.1,
        )

        # 実体は core と auxiliary のみ
        self.assertIsInstance(bundle.core, BundleCore)
        self.assertIsInstance(bundle.auxiliary, BundleAuxiliary)

        # core を直接更新した場合、bundle のプロパティも直ちに追従（二重保持の乖離なし）
        bundle.core.constraint_score = 0.92
        self.assertEqual(bundle.constraint_score, 0.92)
        self.assertEqual(bundle.core_constraint_score, 0.92)

        bundle.core.convergence = 0.88
        self.assertEqual(bundle.convergence, 0.88)
        self.assertEqual(bundle.core_convergence, 0.88)

        # auxiliary を直接更新した場合、bundle のプロパティも直ちに追従
        bundle.auxiliary.constraint_signal = 0.25
        self.assertEqual(bundle.auxiliary_constraint_signal, 0.25)

    def test_runtime_e2e_k_actual_trace_connection_to_probe(self):
        """EnterpriseRuntime の通常運用経路で dispatch_ticket() の actual_replay_token が feedback 経由で Probe まで届くこと (BASE v2.0 §4.2)"""
        from rdl_enterprise.runtime import EnterpriseRuntime
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.snapshot import ReplayToken, BusinessInput

        runtime = EnterpriseRuntime()
        actual_token = ReplayToken(
            token_id="tok_runtime_actual_777",
            model_name="claude-3-opus",
            seed=12345,
            sampling_params={"temperature": 0.0},
        )

        class RuntimeTraceBridge:
            def __init__(self):
                self.received_replay_tokens = []
            def can_replay(self):
                return True
            def resolve_with_trace(self, efp):
                return {"type": "direct_reply", "payload": "runtime_llm_reply"}, actual_token
            def resolve_counterfactual(self, efp, replay_token, counterfactual_input=None):
                self.received_replay_tokens.append(replay_token)
                return {"type": "direct_reply", "payload": "runtime_counterfactual_reply"}

        bridge = RuntimeTraceBridge()
        runtime.cascade.llm_bridge = bridge

        # 1. 参照ノードをグラフに事前登録（Level 3 へフォールバック推論させるため、ノードのexact_keysはクエリに完全には含まれず、意味的・bigram関連性で関連付けられるようにする）
        node_b = MBNode(
            id="node_billing_special",
            domain="billing",
            trigger_pattern={"exact_keys": ["特別契約更新制度"]},
            action_template={"type": "direct_reply", "payload": "runtime_llm_reply"},
            confidence=0.8,
            approval_count=5,
        )
        runtime.mb_graph.commit_node(node_b, origin=CommitmentOrigin.TEST_FIXTURE)

        # 未知チケットを受信し、dispatch_ticket() で Level 3 推論を実行
        efp = BusinessInput(
            ticket_id="ticket_rt_001",
            user_id="user_test",
            category="billing",
            query_text="契約更新の特別措置について教えてください",
        )
        dispatch_res = runtime.dispatch_ticket(efp)
        snap = runtime.pending_snapshots[efp.ticket_id]

        # Level 3 自然推論: matched_node_id は None のままだが、
        # eligible_nodes から constraint_locus_ids が自動設定されていること (BASE v2.0 §4.2)
        self.assertIsNone(snap.f_pred.matched_node_id)
        self.assertIn("node_billing_special", snap.f_pred.constraint_locus_ids)

        from rdl_enterprise.snapshot import FeedbackResult
        feedback = FeedbackResult(
            user_resolved=True,
            human_approved=True,
            feedback_comment="承認済み",
        )
        res_feedback = runtime.resolve_ticket_feedback(
            ticket_id="ticket_rt_001",
            feedback=feedback,
        )

        # RuptureProbe の反実仮想再演で、CaseSnapshot に保存されていた actual_replay_token が bridge に届いたこと
        self.assertGreaterEqual(len(bridge.received_replay_tokens), 1)
        self.assertEqual(bridge.received_replay_tokens[0].token_id, "tok_runtime_actual_777")
        self.assertEqual(bridge.received_replay_tokens[0].conditions_hash, actual_token.conditions_hash)

    def test_level3_fail_closed_contract_without_counterfactual_input_verification(self):
        """CounterfactualInput の受理・介入証跡がない外部推論器は fail-closed で rupture_effect = None (ξ) となること"""
        from rdl_enterprise.cascade import InterpCascade
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RuptureProbe, ConstraintBundle, ConstraintContext
        from rdl_enterprise.snapshot import ReplayToken

        class LegacyBridgeWithoutInterventionTrace:
            """CounterfactualInput を受け取れず、is_mb_dependent フラグもない旧仕様 bridge"""
            def __init__(self):
                self.call_count = 0
            def can_replay(self):
                return True
            def resolve(self, efp):
                return {"type": "direct_reply", "payload": "legacy_base"}
            def resolve_replay(self, efp, replay_token=None):
                self.call_count += 1
                # 介入引数を受け取っていないのに、内部で勝手に揺らいだり変化したりする
                return {"type": "direct_reply", "payload": f"legacy_reply_{self.call_count}"}

        graph = MBGraph()
        bridge = LegacyBridgeWithoutInterventionTrace()
        cascade = InterpCascade(graph, llm_bridge=bridge)
        node = MBNode(
            id="n_legacy",
            domain="general",
            trigger_pattern={"exact_keys": ["レガシー"]},
            action_template={"type": "direct_reply", "payload": "x"},
            confidence=0.8,
            approval_count=10,
        )
        graph.commit_node(node, origin=CommitmentOrigin.TEST_FIXTURE)

        bundle = ConstraintBundle(node_ids=["n_legacy"], locus_type="strong", constraint_score=0.8)
        probe = RuptureProbe()
        efp = _make_efp("未知クエリ", category="general")
        ctx = ConstraintContext(efp=efp, current_time=datetime.utcnow(), llm_bridge=bridge)

        res = probe.probe(bundle, graph, ctx)

        # 介入証跡がないため、Fail-Closed により rupture_effect は None (ξ) にフォールバックする
        self.assertIsNone(res.rupture_effect)
        self.assertFalse(res.intervention_verified)

    def test_rupture_result_audit_view_hashes_and_conditions_hash(self):
        """RuptureResult に conditions_hash, base_mb_view_hash, cut_mb_view_hash が監査証跡として正しく刻印されること"""
        from rdl_enterprise.cascade import InterpCascade
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RuptureProbe, ConstraintBundle, ConstraintContext

        received_cf_inputs = []

        class VerifiedInterventionBridge:
            def can_replay(self):
                return True
            def resolve(self, efp):
                return {"type": "direct_reply", "payload": "base_output"}
            def resolve_counterfactual(self, efp, replay_token, counterfactual_input=None):
                received_cf_inputs.append(counterfactual_input)
                v_hash = counterfactual_input.mb_view.view_hash if (counterfactual_input and counterfactual_input.mb_view) else "cf_view_hash"
                if counterfactual_input and len(counterfactual_input.available_nodes) > 0:
                    return {"type": "direct_reply", "payload": "with_mb", "applied_mb_view_hash": v_hash}
                return {"type": "direct_reply", "payload": "without_mb", "applied_mb_view_hash": v_hash}

        graph = MBGraph()
        n1 = MBNode(
            id="n_audit_1",
            domain="general",
            trigger_pattern={"exact_keys": ["監査キー"]},
            action_template={"type": "direct_reply", "payload": "x"},
            confidence=0.8,
            approval_count=10,
        )
        graph.commit_node(n1, origin=CommitmentOrigin.TEST_FIXTURE)

        bridge = VerifiedInterventionBridge()
        bundle = ConstraintBundle(node_ids=["n_audit_1"], locus_type="strong", constraint_score=0.8)
        probe = RuptureProbe()
        efp = _make_efp("未知クエリ", category="general")
        ctx = ConstraintContext(efp=efp, current_time=datetime.utcnow(), llm_bridge=bridge)

        res = probe.probe(bundle, graph, ctx)

        # 1. 介入が正しく実証されていること
        self.assertTrue(res.intervention_verified)
        self.assertIsNotNone(res.rupture_effect)

        # 2. 外生固定条件 K の conditions_hash が記録されていること
        self.assertIsNotNone(res.conditions_hash)
        self.assertIsInstance(res.conditions_hash, str)
        self.assertGreater(len(res.conditions_hash), 0)

        # 3. 切断前後の M_B ビューハッシュが記録され、かつ互いに異なること (介入の不変証跡)
        self.assertIsNotNone(res.base_mb_view_hash)
        self.assertIsNotNone(res.cut_mb_view_hash)
        self.assertNotEqual(res.base_mb_view_hash, res.cut_mb_view_hash)

    def test_strict_intervention_verification_rejects_self_declared_mb_dependent_without_counterfactual_input(self):
        """is_mb_dependent=True と自己申告していても、CounterfactualInput を受理・適用しない推論器は fail-closed で rupture_effect = None (ξ) となること"""
        from rdl_enterprise.cascade import InterpCascade
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RuptureProbe, ConstraintBundle, ConstraintContext

        class SelfDeclaredBridgeWithoutCFSupport:
            def __init__(self):
                self.is_mb_dependent = True # 自己申告のみ
                self.call_count = 0
            def can_replay(self):
                return True
            def resolve(self, efp):
                return {"type": "direct_reply", "payload": "base"}
            def resolve_counterfactual(self, efp, replay_token):
                # counterfactual_input 引数を受け取れない（適用しない）シグネチャ
                self.call_count += 1
                return {"type": "direct_reply", "payload": f"output_{self.call_count}"}

        graph = MBGraph()
        node = MBNode(
            id="n_self_declare",
            domain="general",
            trigger_pattern={"exact_keys": ["キー"]},
            action_template={"type": "direct_reply", "payload": "x"},
            confidence=0.8,
            approval_count=10,
        )
        graph.commit_node(node, origin=CommitmentOrigin.TEST_FIXTURE)

        bridge = SelfDeclaredBridgeWithoutCFSupport()
        bundle = ConstraintBundle(node_ids=["n_self_declare"], locus_type="strong", constraint_score=0.8)
        probe = RuptureProbe()
        efp = _make_efp("未知クエリ", category="general")
        ctx = ConstraintContext(efp=efp, current_time=datetime.utcnow(), llm_bridge=bridge)

        res = probe.probe(bundle, graph, ctx)
        # 自己申告のみで CounterfactualInput の受領・検証がないため、厳格な fail-closed により None (ξ)
        self.assertIsNone(res.rupture_effect)
        self.assertFalse(res.intervention_verified)

    def test_counterfactual_mb_view_immutable_node_snapshots(self):
        """CounterfactualMBView.nodes が deep-freeze された不変タプルであり、生ノードの変更を受け付けないこと (BASE v2.0 §4.2)"""
        from rdl_enterprise.mb_graph import MBNode
        from rdl_enterprise.snapshot import CounterfactualMBView, CounterfactualInput, BusinessInput, ReplayToken

        node = MBNode(
            id="n_freeze_test",
            domain="security",
            trigger_pattern={"exact_keys": ["秘密キー"]},
            action_template={"type": "direct_reply", "payload": "secret_v1"},
            confidence=0.9,
        )

        view = CounterfactualMBView.from_nodes(
            available_nodes=[node],
            excluded_node_ids=[],
            mb_content_hash="hash_123",
            domain="security",
        )

        # 1. view.nodes はタプルであり、かつ凍結されていること
        self.assertIsInstance(view.nodes, tuple)
        self.assertEqual(len(view.nodes), 1)
        frozen_node = view.nodes[0]
        self.assertTrue(getattr(frozen_node, "is_frozen", False))

        # 凍結ノードの属性直接改変が拒絶されること
        with self.assertRaises(RuntimeError):
            frozen_node.confidence = 0.1

        # 2. 元のノードを変更しても view.nodes には影響しないこと (スナップショット性)
        node.confidence = 0.2
        self.assertEqual(view.nodes[0].confidence, 0.9)

        # 3. CounterfactualInput.available_nodes が mb_view.nodes へ委譲されていること
        token = ReplayToken(token_id="tok_1", model_name="test")
        efp = BusinessInput("T1", "U1", "security", "query")
        cf_input = CounterfactualInput(efp=efp, replay_token=token, mb_view=view)
        self.assertEqual(len(cf_input.available_nodes), 1)
        self.assertEqual(cf_input.available_nodes[0].id, "n_freeze_test")

    def test_interpretation_trace_runtime_lifecycle(self):
        """dispatch_ticket から CaseSnapshot まで InterpretationTrace が一貫して保持されること (BASE v2.0 §4.2)"""
        from rdl_enterprise.runtime import EnterpriseRuntime
        from rdl_enterprise.mb_graph import MBNode
        from rdl_enterprise.snapshot import BusinessInput, InterpretationTrace

        runtime = EnterpriseRuntime()
        node = MBNode(
            id="n_rule_trace",
            domain="hr",
            trigger_pattern={"exact_keys": ["有給休暇"]},
            action_template={"type": "direct_reply", "payload": "申請フォーム"},
            confidence=0.85,
        )
        runtime.mb_graph.commit_node(node, origin=CommitmentOrigin.TEST_FIXTURE)

        # Level 1 ルール推論
        efp = BusinessInput("T_TRACE_01", "U1", "hr", "有給休暇の取り方")
        dispatch_res = runtime.dispatch_ticket(efp)
        snap = runtime.pending_snapshots[efp.ticket_id]

        # pred に constraint_locus_ids が設定されていること
        self.assertEqual(snap.f_pred.matched_node_id, "n_rule_trace")
        self.assertEqual(snap.f_pred.constraint_locus_ids, ["n_rule_trace"])

        # prediction_hash の算出
        pred_hash = InterpretationTrace.compute_prediction_hash(snap.f_pred)
        self.assertIsInstance(pred_hash, str)
        self.assertGreater(len(pred_hash), 0)

    def test_unified_prediction_finalization_and_frozen_context_hash(self):
        """全推論層 (L0-L3, Fallback) の出口が一本化され、FrozenInterpretationContext.context_hash が一貫刻印されること (BASE v2.0 §4.2)"""
        from rdl_enterprise.cascade import InterpCascade, CascadeConfig
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.snapshot import BusinessInput, FrozenInterpretationContext

        graph = MBGraph()
        n0 = MBNode(id="n_l0", domain="hr", trigger_pattern={"exact_keys": ["完全一致クエリ"]}, action_template={"type": "direct_reply", "payload": "L0回答"}, confidence=0.9)
        n1 = MBNode(id="n_l1", domain="hr", trigger_pattern={"rule_expr": r"正規表現.*"}, action_template={"type": "direct_reply", "payload": "L1回答"}, confidence=0.8)
        n2 = MBNode(id="n_l2", domain="hr", trigger_pattern={"exact_keys": ["類似マッチクエリ"]}, action_template={"type": "direct_reply", "payload": "L2回答"}, confidence=0.7)
        graph.commit_node(n0, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n1, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n2, origin=CommitmentOrigin.TEST_FIXTURE)

        frozen_ctx = FrozenInterpretationContext(
            mb_version="v2",
            mb_content_hash="content_hash_fixed_123",
            frozen_mb=graph,
            target_domain="hr",
        )
        cascade = frozen_ctx.create_isolated_cascade()

        # 1. Level 1 (初回到達時は L1 ルール評価され、L0 キャッシュに蓄積される)
        efp0 = BusinessInput("T0", "U1", "hr", "完全一致クエリ")
        p_init = cascade.interpret(efp0)
        self.assertEqual(p_init.cost_tier, 1)

        # 2. Level 0 (2回目の同一クエリは L0 キャッシュにヒット)
        p0 = cascade.interpret(efp0)
        self.assertEqual(p0.cost_tier, 0)
        self.assertIn("interpretation_trace", p0.metadata)
        self.assertEqual(p0.metadata["interpretation_trace"].context_hash, frozen_ctx.context_hash)

        # 3. Level 1 (正規表現)
        efp1 = BusinessInput("T1", "U1", "hr", "正規表現テスト")
        p1 = cascade.interpret(efp1)
        self.assertEqual(p1.cost_tier, 1)
        self.assertIn("interpretation_trace", p1.metadata)
        self.assertEqual(p1.metadata["interpretation_trace"].context_hash, frozen_ctx.context_hash)

        # 4. Fallback (LLMなし)
        efp_fb = BusinessInput("TFB", "U1", "hr", "完全未知クエリxyz")
        pfb = cascade.interpret(efp_fb)
        self.assertEqual(pfb.locus_basis, "fallback")
        self.assertIn("interpretation_trace", pfb.metadata)
        self.assertEqual(pfb.metadata["interpretation_trace"].context_hash, frozen_ctx.context_hash)

    def test_active_constraint_subgraph_and_locus_bundle_matching(self):
        """ContextSelector により活性化サブグラフ L が選出され、locate_bundle_for_locus が 1:1 で L を束ねて切断対象とすること (BASE v2.0 §4.2)"""
        from rdl_enterprise.cascade import InterpCascade, CascadeConfig
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RelationConstraintLocator, ConstraintContext
        from rdl_enterprise.snapshot import BusinessInput

        graph = MBGraph()
        # 5 つのノードを作成
        for i in range(5):
            n = MBNode(
                id=f"node_sub_{i}",
                domain="support",
                trigger_pattern={"exact_keys": [f"問合せ_{i}" if i < 2 else "その他"]},
                action_template={"type": "direct_reply", "payload": f"payload_{i}"},
                confidence=0.5 + 0.1 * i,
                approval_count=10 * i,
            )
            graph.commit_node(n, origin=CommitmentOrigin.TEST_FIXTURE)

        cascade = InterpCascade(graph)
        efp = BusinessInput("T_SUB", "U1", "support", "問合せ_0 について教えて")

        # ContextSelector の選出 (limit=2)
        eligible = graph.list_nodes()
        selected = cascade.select_active_constraint_subgraph(eligible, efp, limit=2)
        self.assertEqual(len(selected), 2)
        self.assertEqual(selected[0].id, "node_sub_0")

        # locate_bundle_for_locus による拘束束構築
        locus_ids = [s.id for s in selected]
        locator = RelationConstraintLocator()
        ctx = ConstraintContext(efp=efp, active_domain="support")
        bundle = locator.locate_bundle_for_locus(graph, locus_ids, ctx)

        # 1. 明示的 support 関係がない場合:
        # node_sub_0 が primary となり、暗黙ノード node_sub_1 は inferred_node_ids (auxiliary ξ) へ分離される
        self.assertEqual(bundle.node_ids, [locus_ids[0]])
        self.assertEqual(bundle.primary_node_id(), locus_ids[0])
        self.assertEqual(bundle.inferred_node_ids, [locus_ids[1]])
        self.assertGreater(bundle.constraint_score, 0.0)
        self.assertGreater(bundle.auxiliary_constraint_signal, 0.0)

        # 2. 明示的 support 関係がある場合:
        # node_sub_0 が node_sub_1 を support している場合、両ノードが core bundle.node_ids に入る
        n0 = graph.get("node_sub_0")
        n0.node_relations = {"node_sub_1": "support"}
        bundle_with_support = locator.locate_bundle_for_locus(graph, locus_ids, ctx)
        self.assertEqual(bundle_with_support.node_ids, locus_ids)
        self.assertEqual(bundle_with_support.supporting_node_ids, locus_ids[1:])
        self.assertEqual(bundle_with_support.inferred_node_ids, [])

    def test_applied_view_hash_contract_and_trace_verification(self):
        """RuptureProbe が BridgeExecutionTrace の 4点照合を検証し、満たした場合のみ intervention_verified=True とし effect_verified_locus_ids を記録すること (BASE v2.0 §4.2)"""
        from rdl_enterprise.cascade import InterpCascade
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RuptureProbe, ConstraintBundle, ConstraintContext
        from rdl_enterprise.snapshot import BusinessInput, ReplayToken, CounterfactualInput, BridgeExecutionTrace

        class VerifiedTraceMockBridge:
            def __init__(self):
                self.is_mb_dependent = True
                self.applied_mb_view_hash = None
                self._token = ReplayToken(token_id="tok_fixed", model_name="v-model", seed=42)
            def can_replay(self):
                return True
            def create_replay_token(self):
                return self._token
            def resolve(self, efp, mb_view=None):
                v_hash = mb_view.view_hash if mb_view else "base_view_hash"
                trace = BridgeExecutionTrace(
                    requested_mb_view_hash=v_hash,
                    applied_mb_view_hash=v_hash, # 正しく一致
                    conditions_hash=self._token.conditions_hash,
                    applied_node_ids=("n_tgt",),
                )
                return {
                    "type": "direct_reply",
                    "payload": "base_with_tgt",
                    "applied_mb_view_hash": v_hash,
                    "execution_trace": trace,
                    "replay_token": self._token,
                }
            def resolve_counterfactual(self, cf_input: CounterfactualInput):
                v_hash = cf_input.mb_view.view_hash if cf_input.mb_view else "view_hash"
                is_cut = bool(cf_input.excluded_node_ids)
                applied_ids = () if is_cut else ("n_tgt",)
                trace = BridgeExecutionTrace(
                    requested_mb_view_hash=v_hash,
                    applied_mb_view_hash=v_hash, # 正しく一致
                    conditions_hash=self._token.conditions_hash,
                    applied_node_ids=applied_ids,
                )
                payload_text = "cut_without_tgt_different" if is_cut else "base_with_tgt"
                return {
                    "type": "direct_reply",
                    "payload": payload_text,
                    "applied_mb_view_hash": v_hash,
                    "execution_trace": trace,
                }

        graph = MBGraph()
        n_tgt = MBNode(id="n_tgt", domain="sales", trigger_pattern={"exact_keys": ["特別割引"]}, action_template={"type": "direct_reply", "payload": "10%割引"}, confidence=0.8)
        graph.commit_node(n_tgt, origin=CommitmentOrigin.TEST_FIXTURE)

        bridge = VerifiedTraceMockBridge()
        bundle = ConstraintBundle(node_ids=["n_tgt"], locus_type="strong", constraint_score=0.8)
        probe = RuptureProbe()
        # ローカルルール (Level 1) にヒットしないクエリで推論させ、Level 3 の bridge を呼び出させる
        efp = BusinessInput("T_TGT", "U1", "sales", "未定義の複雑な割引相談")
        ctx = ConstraintContext(efp=efp, llm_bridge=bridge, active_domain="sales")

        res = probe.probe(bundle, graph, ctx)

        # 4点照合が成立し、介入が実証されること
        self.assertTrue(res.intervention_verified)
        self.assertIsNotNone(res.rupture_effect)
        self.assertGreater(res.rupture_effect, 0.0)
        self.assertEqual(res.effect_verified_locus_ids, ["n_tgt"])
        self.assertIsNotNone(res.base_trace_id)
        self.assertIsNotNone(res.cut_trace_id)
        self.assertNotEqual(res.base_mb_view_hash, res.cut_mb_view_hash)


    def test_applied_view_hash_no_fabrication_fail_closed(self):
        """P0 契約: Bridge が applied_mb_view_hash / execution_trace を明示報告しない場合、requested を代入・捏造せず Fail-Closed (intervention_verified=False, rupture_effect=None) とすること (BASE v2.0 §4.2)"""
        from rdl_enterprise.cascade import InterpCascade
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RuptureProbe, ConstraintBundle, ConstraintContext
        from rdl_enterprise.snapshot import BusinessInput, ReplayToken, CounterfactualInput

        class SilentBridgeWithoutAppliedHash:
            """applied_mb_view_hash も execution_trace も返さない Bridge"""
            def __init__(self):
                self.is_mb_dependent = True
                self._token = ReplayToken(token_id="tok_silent", model_name="v-model", seed=42)
            def can_replay(self):
                return True
            def create_replay_token(self):
                return self._token
            def resolve(self, efp, mb_view=None):
                return {
                    "type": "direct_reply",
                    "payload": "base_silent_reply",
                    # applied_mb_view_hash, execution_trace をあえて含めない
                }
            def resolve_counterfactual(self, cf_input: CounterfactualInput):
                is_cut = bool(cf_input.excluded_node_ids)
                return {
                    "type": "direct_reply",
                    "payload": "cut_silent_reply" if is_cut else "base_silent_reply",
                    # applied_mb_view_hash, execution_trace をあえて含めない
                }

        graph = MBGraph()
        n_tgt = MBNode(id="n_tgt", domain="sales", trigger_pattern={"exact_keys": ["特別割引"]}, action_template={"type": "direct_reply", "payload": "10%割引"}, confidence=0.8)
        graph.commit_node(n_tgt, origin=CommitmentOrigin.TEST_FIXTURE)

        bridge = SilentBridgeWithoutAppliedHash()
        bundle = ConstraintBundle(node_ids=["n_tgt"], locus_type="strong", constraint_score=0.8, freshness=1.0, relevance=0.8)
        probe = RuptureProbe()
        efp = BusinessInput("T_SILENT", "U1", "sales", "未定義の複雑な割引相談")
        ctx = ConstraintContext(efp=efp, llm_bridge=bridge, active_domain="sales")

        res = probe.probe(bundle, graph, ctx)

        # 捏造バイパスが禁止され、Fail-Closed (未検証 ξ) となること
        self.assertFalse(res.intervention_verified)
        self.assertIsNone(res.rupture_effect)
        self.assertEqual(res.verdict, "unresolved")

    def test_context_selector_relevance_floor_and_propagation(self):
        """P3 契約: ContextSelector が relevance_floor (0.05) で無関係ノードを足切りし、明示的 support 関係から 1-hop 伝播すること (BASE v2.0 §4.2)"""
        from rdl_enterprise.cascade import InterpCascade
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.snapshot import BusinessInput

        graph = MBGraph()
        # 1. 直接適合ノード (rel >= 0.05)
        n_seed = MBNode(
            id="node_seed",
            domain="sales",
            trigger_pattern={"exact_keys": ["大型契約"]},
            action_template={"type": "direct_reply", "payload": "seed"},
            confidence=0.7,
            approval_count=10,
        )
        # 2. 適合ゼロだが seed から明示的 support されているノード
        n_supported = MBNode(
            id="node_supported",
            domain="sales",
            trigger_pattern={"exact_keys": ["完全無関係キーXYZ"]},
            action_template={"type": "direct_reply", "payload": "supported"},
            confidence=0.8,
            approval_count=5,
        )
        n_seed.node_relations = {"node_supported": "support"}

        # 3. 適合ゼロで関係性もないが、承認数が極めて高い孤立ノード
        n_high_approval_isolated = MBNode(
            id="node_high_approval_isolated",
            domain="sales",
            trigger_pattern={"exact_keys": ["完全無関係キーABC"]},
            action_template={"type": "direct_reply", "payload": "isolated"},
            confidence=0.95,
            approval_count=1000, # 圧倒的な承認実績
        )

        graph.commit_node(n_seed, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n_supported, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n_high_approval_isolated, origin=CommitmentOrigin.TEST_FIXTURE)

        cascade = InterpCascade(graph)
        efp = BusinessInput("T_PROP", "U1", "sales", "大型契約についての相談")

        selected = cascade.select_active_constraint_subgraph(graph.list_nodes(), efp, limit=3)
        selected_ids = [n.id for n in selected]

        # seed が選ばれる
        self.assertIn("node_seed", selected_ids)
        # 適合ゼロでも seed から 1-hop support 伝播されたノードが含まれる
        self.assertIn("node_supported", selected_ids)
        # 承認数がどれだけ高くても、関連性ゼロかつ孤立したノードは活性化サブグラフから除外される
        self.assertNotIn("node_high_approval_isolated", selected_ids)

    def test_relation_breadth_changes_active_view_without_mutating_graph(self):
        """Basic breadth caps same-hop selection; it does not alter depth, rank, or M_B relations."""
        from copy import deepcopy
        from rdl_enterprise.cascade import InterpCascade, CascadeConfig
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.snapshot import BusinessInput

        graph = MBGraph()
        seed = MBNode(
            id="breadth-seed",
            domain="support",
            trigger_pattern={"exact_keys": ["契約"]},
            action_template={"type": "direct_reply", "payload": "seed"},
            confidence=0.8,
            approval_count=10,
        )
        related = []
        for index in range(3):
            node = MBNode(
                id=f"breadth-related-{index}",
                domain="support",
                trigger_pattern={"exact_keys": [f"無関係-{index}"]},
                action_template={"type": "direct_reply", "payload": str(index)},
                confidence=0.6,
                approval_count=3 - index,
            )
            related.append(node)
        seed.node_relations = {node.id: "support" for node in related}
        graph.commit_node(seed, origin=CommitmentOrigin.TEST_FIXTURE)
        for node in related:
            graph.commit_node(node, origin=CommitmentOrigin.TEST_FIXTURE)

        before = deepcopy(graph)
        efp = BusinessInput("T_BREADTH", "U1", "support", "契約について")
        narrow = InterpCascade(graph, config=CascadeConfig(relation_breadth_limit=1))
        wide = InterpCascade(graph, config=CascadeConfig(relation_breadth_limit=3))

        narrow_ids = [node.id for node in narrow.select_active_constraint_subgraph(graph.list_nodes(), efp)]
        wide_ids = [node.id for node in wide.select_active_constraint_subgraph(graph.list_nodes(), efp)]

        self.assertEqual(narrow_ids, ["breadth-seed"])
        self.assertEqual(wide_ids, ["breadth-seed", "breadth-related-0", "breadth-related-1"])
        self.assertEqual(graph.get("breadth-seed").node_relations, before.get("breadth-seed").node_relations)
        self.assertEqual(tuple(node.id for node in graph.list_nodes()), tuple(node.id for node in before.list_nodes()))
        self.assertEqual(wide.config.relation_breadth_limit, 3)

    def test_relation_breadth_does_not_change_hop_or_ranking(self):
        """Changing breadth selects a prefix of the same ranked one-hop view."""
        from rdl_enterprise.cascade import InterpCascade, CascadeConfig
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.snapshot import BusinessInput

        graph = MBGraph()
        seed = MBNode(
            id="rank-seed",
            domain="support",
            trigger_pattern={"exact_keys": ["障害"]},
            action_template={"type": "direct_reply", "payload": "seed"},
            confidence=0.8,
            approval_count=10,
        )
        children = []
        for index in range(3):
            child = MBNode(
                id=f"rank-child-{index}",
                domain="support",
                trigger_pattern={"exact_keys": [f"child-{index}"]},
                action_template={"type": "direct_reply", "payload": str(index)},
                confidence=0.5,
                approval_count=index + 1,
            )
            children.append(child)
        seed.node_relations = {child.id: "support" for child in children}
        graph.commit_node(seed, origin=CommitmentOrigin.TEST_FIXTURE)
        for child in children:
            graph.commit_node(child, origin=CommitmentOrigin.TEST_FIXTURE)

        efp = BusinessInput("T_BREADTH_RANK", "U1", "support", "障害について")
        cascade = InterpCascade(graph, config=CascadeConfig(relation_breadth_limit=2))
        selected = cascade.select_active_constraint_subgraph(graph.list_nodes(), efp)
        self.assertEqual([node.id for node in selected], ["rank-seed", "rank-child-2"])
        self.assertEqual(len(cascade.select_active_constraint_subgraph(graph.list_nodes(), efp, limit=1)), 2)

    def test_bundle_level_vs_node_level_ablation(self):
        """P4/P5 契約: 複数ノード束 L において、束全体の切断で ΔF > 0 が生じた場合でも、単一ノード個別切断 (Leave-One-Out) により effect_verified_node_ids を分離・限定すること (BASE v2.0 §4.2)"""
        from rdl_enterprise.mb_graph import MBGraph, MBNode, CommitmentOrigin
        from rdl_enterprise.constraint import RuptureProbe, ConstraintBundle, ConstraintContext
        from rdl_enterprise.snapshot import BusinessInput, ReplayToken, CounterfactualInput, BridgeExecutionTrace

        class MultiNodeAblationMockBridge:
            """
            n1 + n2 の両方が存在するときの基本推論: base
            n1 が切断されると: cut_n1 (変化あり: diff > 0)
            n2 だけが切断されても: base (変化なし: n1 があれば同一動作)
            n1 + n2 の両方が切断されると: cut_both (変化あり: diff > 0)
            """
            def __init__(self):
                self.is_mb_dependent = True
                self.applied_mb_view_hash = None
                self._token = ReplayToken(token_id="tok_ablation", model_name="v-model", seed=42)
            def can_replay(self):
                return True
            def create_replay_token(self):
                return self._token
            def resolve(self, efp, mb_view=None):
                v_hash = mb_view.view_hash if mb_view else "base_view"
                trace = BridgeExecutionTrace(
                    requested_mb_view_hash=v_hash,
                    applied_mb_view_hash=v_hash,
                    conditions_hash=self._token.conditions_hash,
                    applied_node_ids=("n1", "n2"),
                )
                return {
                    "type": "direct_reply",
                    "payload": "base_behavior",
                    "applied_mb_view_hash": v_hash,
                    "execution_trace": trace,
                    "replay_token": self._token,
                }
            def resolve_counterfactual(self, cf_input: CounterfactualInput):
                v_hash = cf_input.mb_view.view_hash if cf_input.mb_view else "cut_view"
                excluded = set(cf_input.excluded_node_ids)
                # n1 が除外されている場合のみ挙動が変わる
                if "n1" in excluded:
                    payload = "behavior_changed_without_n1"
                else:
                    payload = "base_behavior"
                trace = BridgeExecutionTrace(
                    requested_mb_view_hash=v_hash,
                    applied_mb_view_hash=v_hash,
                    conditions_hash=self._token.conditions_hash,
                    applied_node_ids=tuple(n for n in ("n1", "n2") if n not in excluded),
                )
                return {
                    "type": "direct_reply",
                    "payload": payload,
                    "applied_mb_view_hash": v_hash,
                    "execution_trace": trace,
                }

        graph = MBGraph()
        n1 = MBNode(id="n1", domain="tech", trigger_pattern={"exact_keys": ["API仕様"]}, action_template={"type": "direct_reply", "payload": "p1"}, confidence=0.9)
        n2 = MBNode(id="n2", domain="tech", trigger_pattern={"exact_keys": ["API補足"]}, action_template={"type": "direct_reply", "payload": "p2"}, confidence=0.8)
        n1.node_relations = {"n2": "support"}
        graph.commit_node(n1, origin=CommitmentOrigin.TEST_FIXTURE)
        graph.commit_node(n2, origin=CommitmentOrigin.TEST_FIXTURE)

        bridge = MultiNodeAblationMockBridge()
        bundle = ConstraintBundle(node_ids=["n1", "n2"], locus_type="strong", constraint_score=0.85)
        probe = RuptureProbe()
        efp = BusinessInput("T_ABL", "U1", "tech", "未知のAPI問い合わせ")
        ctx = ConstraintContext(efp=efp, llm_bridge=bridge, active_domain="tech")

        res = probe.probe(bundle, graph, ctx)

        # 1. 介入検証は合格
        self.assertTrue(res.intervention_verified)
        self.assertIsNotNone(res.rupture_effect)
        self.assertGreater(res.rupture_effect, 0.0)

        # 2. 束集合レベル (set-level) では [n1, n2] の除去で効果が実証されている
        self.assertEqual(res.effect_verified_bundle_ids, ["n1", "n2"])

        # 3. ノード個別レベル (node-level ablation) では、単独除去で効果があった n1 のみが特定され、
        #    単独除去では効果がなかった n2 は過大帰属されずに除外されていること！
        self.assertEqual(res.effect_verified_node_ids, ["n1"])


if __name__ == "__main__":
    unittest.main()



