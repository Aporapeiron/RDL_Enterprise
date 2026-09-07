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
    履歴破断検査:
    過去の成功実績（Golden History）を候補 M_B' に流し、
    以前は成功していた案件が改悪・解決不能になっていないか（リグレッション）を検証する
    """
    name = "RegressionHistoryChecker"

    def test(self, candidate_mb: MBGraph, history: List[CaseSnapshot]) -> DurabilityReport:
        success_history = [s for s in history if s.status == CaseStatus.SUCCESS]
        if not success_history:
            return DurabilityReport(self.name, passed=True, score=1.0, break_points=[], details={"tested": 0})

        failures = []
        # 簡易判定：過去にヒットしていたノードが候補 M_B' に存在し、内容が空でないか
        for snap in success_history:
            orig_nid = snap.f_pred.matched_node_id
            if orig_nid:
                node = candidate_mb.get(orig_nid)
                if not node or not node.action_template.get("payload"):
                    failures.append(f"Ticket {snap.efp.ticket_id}: ノード {orig_nid} が欠落または無効化されています")

        score = (len(success_history) - len(failures)) / len(success_history)
        return DurabilityReport(
            checker_name=self.name,
            passed=(len(failures) == 0),
            score=score,
            break_points=failures,
            details={"tested": len(success_history), "failed": len(failures)},
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
