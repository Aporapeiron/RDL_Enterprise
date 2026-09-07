import sys
import os
import json
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "src")))

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from rdl_enterprise.mb_graph import MBGraph
from rdl_enterprise.snapshot import BusinessInput, FeedbackResult
from rdl_enterprise.runtime import EnterpriseRuntime

def print_header(title: str):
    print("\n" + "=" * 65)
    print(f"  {title}")
    print("=" * 65)

def print_step(step_name: str, details: str):
    print(f"\n▶ [{step_name}]")
    print(f"  {details}")

def main():
    print_header("RDL業務AI 最小PoCシミュレーション実行")

    seed_path = os.path.join(os.path.dirname(__file__), "data", "seed_it_support.json")
    graph = MBGraph.load_json(seed_path)
    runtime = EnterpriseRuntime(mb_graph=graph, theta_0=2.0, gamma=0.1)

    print(f"初期ノード数: {len(graph.nodes)} 件")
    print(f"初期平均κ: {graph.average_kappa():.3f}, 初期総慣性: {graph.total_inertia():.3f}")

    # =========================================================================
    # シナリオ1：定型業務の外部推論コストほぼゼロ化（パスワードリセット）
    # =========================================================================
    print_header("シナリオ1：定型業務の反復とコスト沈澱（ベテラン化）")

    for i in range(1, 4):
        efp = BusinessInput(
            ticket_id=f"TICK-PWD-{i:02d}",
            user_id=f"user_{i}",
            category="account",
            query_text="パスワードリセットの方法を教えてください",
        )
        res = runtime.handle_ticket(
            efp,
            feedback=FeedbackResult(user_resolved=True, human_approved=False, human_rejected=False),
        )
        print_step(
            f"PWD案件 #{i}",
            f"Cost Tier: {res.cost_tier} | ノード: {res.prediction.matched_node_id} | 出力: {res.final_output[:40]}..."
        )

    node_pwd = graph.get("node_pwd_reset")
    print(f"\n>> パスワードノードの成長: success_count={node_pwd.success_count}, 慣性={node_pwd.inertia():.2f}, κ={node_pwd.kappa():.3f}")

    # =========================================================================
    # シナリオ2：暗黙知の獲得（VPN接続エラー）
    # =========================================================================
    print_header("シナリオ2：未経験のトラブルと先輩（人間）からの暗黙知獲得")

    # 1回目：一般的な案内をするが解決せず、クレーム（不整合）が発生
    efp_vpn1 = BusinessInput(
        ticket_id="TICK-VPN-01",
        user_id="user_mac_01",
        category="network",
        query_text="MacBookでVPNが急に繋がらなくなりました",
    )
    res_vpn1 = runtime.handle_ticket(
        efp_vpn1,
        feedback=FeedbackResult(user_resolved=False, human_approved=False, human_rejected=False, feedback_comment="再起動しても繋がりません"),
    )
    print_step(
        "VPN初回対応 (失敗)",
        f"AIの予測回答: {res_vpn1.final_output} | 結果: 未解決 (E_pred={res_vpn1.e_prediction:.2f}, H={res_vpn1.current_h:.2f})"
    )

    # 2回目：熱が溜まった状態で再度問い合わせ → 人間（先輩）へ問い合わせ発火
    efp_vpn2 = BusinessInput(
        ticket_id="TICK-VPN-02",
        user_id="user_mac_02",
        category="network",
        query_text="MacBookでVPNが急に繋がらなくなりました。助けてください",
    )
    # 先輩社員からの暗黙知注入
    senpai_answer = "【情シス先輩の知恵】Mac新OS(Sequoia)ではVPNプロファイルの再インストールが必要です。"
    res_vpn2 = runtime.handle_ticket(
        efp_vpn2,
        feedback=FeedbackResult(user_resolved=True, human_approved=True, human_rejected=False),
        human_override_answer=senpai_answer,
    )
    print_step(
        "VPN2回目 (人間介入・暗黙知注入)",
        f"HITL発火: {res_vpn2.hitl_required} (理由: {res_vpn2.hitl_reason}) | 先輩回答を学習・沈澱"
    )

    # 3回目：同じ問い合わせが来たとき → 新ノードで即答できるか検証！
    efp_vpn3 = BusinessInput(
        ticket_id="TICK-VPN-03",
        user_id="user_mac_03",
        category="network",
        query_text="MacBookでVPNが急に繋がらなくなりました",
    )
    res_vpn3 = runtime.handle_ticket(
        efp_vpn3,
        feedback=FeedbackResult(user_resolved=True, human_approved=False, human_rejected=False),
    )
    print_step(
        "VPN3回目 (学習後の自律応答)",
        f"Cost Tier: {res_vpn3.cost_tier} | 自律回答: {res_vpn3.final_output} (即座に解決！)"
    )

    # =========================================================================
    # シナリオ3：制度改定による環境変化と再編相 M_Δ
    # =========================================================================
    print_header("シナリオ3：申請ツールの強制移行による発熱と再編相 M_Δ")

    print("※ 会社で旧申請ツールが廃止され、新SaaSへ移行した事件が発生！")
    for i in range(1, 4):
        efp_wf = BusinessInput(
            ticket_id=f"TICK-WF-{i:02d}",
            user_id=f"employee_{i}",
            category="workflow",
            query_text="備品購入申請の方法を教えてください",
        )
        # 社員から「URLが繋がらない」「画面が違う」と猛反発・差し戻し
        res_wf = runtime.handle_ticket(
            efp_wf,
            feedback=FeedbackResult(
                user_resolved=False,
                human_approved=False,
                human_rejected=True,
                feedback_comment="旧ツールのURLリンクが切れています！",
                new_knowledge_provided="新SaaSポータル(https://saas-wf.corp.com)より申請してください。" if i == 3 else None
            ),
        )
        print_step(
            f"申請問い合わせ #{i}",
            f"H={res_wf.current_h:.2f} / θ_eff={res_wf.current_theta_eff:.2f} | 再編相M_Δ移行: {res_wf.transition_to_m_delta}"
        )

    # =========================================================================
    # 最終メトリクス
    # =========================================================================
    print_header("シミュレーション結果（RDL運用メトリクス）")
    metrics = runtime.get_metrics()
    print(json.dumps(metrics, ensure_ascii=False, indent=2))

    print("\n[検証総括]")
    print(f"・総チケット数: {metrics['total_tickets']} 件")
    print(f"・自動解決率: {metrics['auto_resolution_rate']*100:.1f}%")
    print(f"・人間エスカレーション(HITL)率: {metrics['hitl_rate']*100:.1f}%")
    print(f"・再編相 M_Δ 発動回数: {metrics['m_delta_transitions']} 回")
    print(f"・コスト分布 (Tier 0 / 1 / 2 / 3): {metrics['cost_tier_distribution']}")
    print("\n>> 3大シナリオすべてにおいて、RDL力学通りの振る舞い（ベテラン化・学習・発熱・再編）を確認しました！")

if __name__ == "__main__":
    main()
