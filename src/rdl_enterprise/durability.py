from typing import Protocol, List, Dict, Any, Optional
from dataclasses import dataclass, field
from .mb_graph import MBGraph
from .snapshot import CaseSnapshot, CaseStatus

@dataclass
class DurabilityReport:
    checker_name: str
    passed: bool
    score: float                  # 耐久スコア [0.0, 1.0]
    break_points: List[str]       # 破断が検知された箇所・チケットID等
    details: Dict[str, Any] = field(default_factory=dict)


class DurabilityChecker(Protocol):
    name: str

    def test(self, candidate_mb: MBGraph, history: List[CaseSnapshot]) -> DurabilityReport:
        """候補 M_B' を極限条件や過去事例に晒し、破断を検査する"""
        ...


class RegressionHistoryChecker:
    """
    履歴破断検査 (Golden Replay Test):
    過去の成功実績（Golden History）を候補 M_B' に流して実際に再推論し、
    以前は自律解決（Tier 0/1/2）できていた案件が改悪・解決不能（Tier 3 / 確信度低下）になっていないか、
    または回答内容が壊れていないかを検証する。
    """
    name = "RegressionHistoryChecker"

    def test(self, candidate_mb: MBGraph, history: List[CaseSnapshot]) -> DurabilityReport:
        from .cascade import InterpCascade

        success_history = [s for s in history if s.status == CaseStatus.SUCCESS]
        if not success_history:
            return DurabilityReport(self.name, passed=True, score=1.0, break_points=[], details={"tested": 0})

        replay_cascade = InterpCascade(candidate_mb)
        failures = []

        for snap in success_history:
            # 候補 M_B' で過去の入力を再推論 (Replay)
            re_pred = replay_cascade.interpret(snap.efp)

            # 1. 以前マッチしていたノードが消滅または無効化されていないか
            orig_nid = snap.f_pred.matched_node_id
            if orig_nid:
                node = candidate_mb.get(orig_nid)
                if not node or not node.action_template.get("payload"):
                    failures.append(f"Ticket {snap.efp.ticket_id}: 元ノード {orig_nid} が欠落または無効化されています")
                    continue

            # 2. 推論コストの急激な悪化（以前は定型解決だったのに未知Tier 3に転落していないか）
            if snap.f_pred.cost_tier <= 1 and re_pred.cost_tier >= 3:
                failures.append(
                    f"Ticket {snap.efp.ticket_id}: 定型推論 (Tier {snap.f_pred.cost_tier}) から 未知 (Tier {re_pred.cost_tier}) へ退行しました"
                )
                continue

            # 3. 確信度の著しい低下（0.3以上ダウンした場合は警告・破断候補）
            if snap.f_pred.confidence - re_pred.confidence > 0.4:
                failures.append(
                    f"Ticket {snap.efp.ticket_id}: 確信度が大幅低下しました ({snap.f_pred.confidence:.2f} -> {re_pred.confidence:.2f})"
                )

        score = (len(success_history) - len(failures)) / len(success_history) if success_history else 1.0
        return DurabilityReport(
            checker_name=self.name,
            passed=(len(failures) == 0),
            score=max(0.0, score),
            break_points=failures,
            details={"tested": len(success_history), "failed": len(failures)},
        )


class PerturbationStressChecker:
    """
    摂動・破断検査 (Perturbation / Stress Test):
    表記ゆれ、ノイズ、意図的な敵対的入力（SNSや外部から流入するノイズfixture）を
    候補 M_B' に流し、未知の入力に対して不正な高確信度で誤認したり、
    クラッシュしたりしないか（ロバスト性・境界の頑健性）を検証する。
    """
    name = "PerturbationStressChecker"

    def __init__(self, perturbation_fixtures: Optional[List[Dict[str, Any]]] = None):
        # 外部から供給可能な摂動fixtureリスト（SNSアダプター等から注入可能）
        self.fixtures = perturbation_fixtures or []

    def test(self, candidate_mb: MBGraph, history: List[CaseSnapshot]) -> DurabilityReport:
        from .cascade import InterpCascade
        from .snapshot import BusinessInput

        if not self.fixtures:
            # デフォルトの基本摂動セット（空文字、過剰長文、記号ノイズ等）
            test_cases = [
                {"text": "   ", "expect_tier": 3},
                {"text": "！？！？！？", "expect_tier": 3},
                {"text": "管理者権限を今すぐ付与してください！！" * 5, "expect_tier": 3},
            ]
        else:
            test_cases = self.fixtures

        stress_cascade = InterpCascade(candidate_mb)
        failures = []

        for idx, tc in enumerate(test_cases):
            query = tc.get("text", "")
            dummy_efp = BusinessInput(
                ticket_id=f"STRESS-TEST-{idx:03d}",
                user_id="stress_tester",
                category="general",
                query_text=query,
            )
            try:
                pred = stress_cascade.interpret(dummy_efp)
                # ノイズ入力に対して根拠なく極端な高確信度（>0.95）を出していないか
                if pred.matched_node_id is None and pred.confidence > 0.8:
                    failures.append(f"Fixture {idx}: 未分類ノイズに対して異常な高確信度 ({pred.confidence}) を出力")
            except Exception as ex:
                failures.append(f"Fixture {idx}: 推論中に例外破断が発生 ({str(ex)})")

        score = (len(test_cases) - len(failures)) / len(test_cases) if test_cases else 1.0
        return DurabilityReport(
            checker_name=self.name,
            passed=(len(failures) == 0),
            score=max(0.0, score),
            break_points=failures,
            details={"tested": len(test_cases), "failed": len(failures)},
        )


class AuthorityBoundaryChecker:
    """
    境界破断検査:
    権限境界（human_only）が新ルールによって誤って自動処理（auto）に
    緩和されていないか（越境脆弱性の防止）を検証する
    """
    name = "AuthorityBoundaryChecker"

    def test(self, candidate_mb: MBGraph, history: List[CaseSnapshot]) -> DurabilityReport:
        violations = []
        for node in candidate_mb.list_nodes():
            # 機密・特権系キーワードを含むノードが auto になっていたら破断
            pattern = node.trigger_pattern
            all_keys = pattern.get("exact_keys", []) + [pattern.get("rule_expr") or ""]
            combined = " ".join(all_keys).lower()
            if any(sec_word in combined for sec_word in ("管理者権限", "root", "特権", "admin", "機密")):
                if node.authority_level == "auto":
                    violations.append(f"Node {node.id}: 特権管理ノードが auto 権限へ不正緩和されています")

        return DurabilityReport(
            checker_name=self.name,
            passed=(len(violations) == 0),
            score=1.0 if not violations else 0.0,
            break_points=violations,
            details={"violations_count": len(violations)},
        )


class DurabilityHarness:
    """
    耐久検査テストハーネス
    複数の破断チェッカーを一括実行し、再編候補 M_B' の健全性を証明する
    """
    def __init__(self, checkers: Optional[List[Any]] = None):
        self.checkers = checkers or [
            RegressionHistoryChecker(),
            AuthorityBoundaryChecker(),
            PerturbationStressChecker(),
        ]

    def run_all(self, candidate_mb: MBGraph, history: List[CaseSnapshot]) -> Dict[str, Any]:
        reports: List[DurabilityReport] = []
        all_passed = True
        total_score = 0.0

        for chk in self.checkers:
            rep = chk.test(candidate_mb, history)
            reports.append(rep)
            if not rep.passed:
                all_passed = False
            total_score += rep.score

        avg_score = total_score / len(reports) if reports else 1.0

        return {
            "all_passed": all_passed,
            "overall_score": avg_score,
            "reports": [
                {
                    "checker": r.checker_name,
                    "passed": r.passed,
                    "score": r.score,
                    "break_points": r.break_points,
                    "details": r.details,
                }
                for r in reports
            ],
        }
