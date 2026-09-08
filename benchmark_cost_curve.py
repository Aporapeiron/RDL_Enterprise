import sys
import os
import json
import time
from typing import Dict, List, Any

# Ensure src is on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "src")))

if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rdl_enterprise.mb_graph import MBGraph, MBNode
from rdl_enterprise.snapshot import BusinessInput, FeedbackResult
from rdl_enterprise.runtime import EnterpriseRuntime

TIER_COST_MAP = {
    0: 0,       # Local memory hit
    1: 0,       # Local rule match
    2: 300,     # Semantic search / embedding lookup
    3: 1800,    # Heavy LLM inference
}

def run_benchmark():
    print("=" * 70)
    print("  RDL Enterprise Cost-Curve Comparative Benchmark")
    print("  (Pure LLM vs Standard RAG vs RDL Enterprise Closed Loop)")
    print("=" * 70)

    seed_path = os.path.join(os.path.dirname(__file__), "data", "seed_it_support.json")
    graph = MBGraph.load_json(seed_path)
    runtime = EnterpriseRuntime(mb_graph=graph, theta_0=2.0, gamma=0.1)

    queries = [
        ("account", "パスワードリセットの方法を教えてください"),
        ("account", "パスワード初期化の手順を教えて"),
        ("account", "パスワードを忘れたので再発行したい"),
        ("hardware", "ディスプレイが映りません HDMI接続"),
        ("hardware", "外部モニターの接続方法"),
        ("network", "VPN接続エラー 691"),
        ("network", "社外からVPNに接続できません"),
        ("security", "特権アカウントのアクセス申請書提出先"),
        ("hardware", "社用PCのバッテリー膨張交換"),
    ]

    total_tickets = 60
    workload = []
    for i in range(total_tickets):
        if i % 5 != 0:
            q_idx = i % 7
        else:
            q_idx = 7 + (i % 2)
        cat, text = queries[q_idx]
        workload.append(BusinessInput(
            ticket_id=f"BENCH-{i:03d}",
            user_id=f"emp_{i % 15}",
            category=cat,
            query_text=text
        ))

    llm_tokens = []
    llm_cum = 0
    for _ in workload:
        llm_cum += TIER_COST_MAP[3]
        llm_tokens.append(llm_cum)

    rag_token_per_call = 1000
    rag_tokens = []
    rag_cum = 0
    for _ in workload:
        rag_cum += rag_token_per_call
        rag_tokens.append(rag_cum)

    rdl_tokens = []
    rdl_tiers = []
    rdl_cum = 0

    for idx, efp in enumerate(workload):
        result = runtime.handle_ticket(
            efp,
            feedback=FeedbackResult(user_resolved=True, human_approved=False, human_rejected=False)
        )
        cost = TIER_COST_MAP.get(result.cost_tier, 0)
        rdl_cum += cost
        rdl_tokens.append(rdl_cum)
        rdl_tiers.append(result.cost_tier)

    print(f"\nCompleted {total_tickets} sequential tickets.")
    print("-" * 70)
    print(f"{'Metric':<30} | {'Pure LLM':<12} | {'Standard RAG':<12} | {'RDL Enterprise':<12}")
    print("-" * 70)
    print(f"{'Total Tokens Consumed':<30} | {llm_tokens[-1]:<12,d} | {rag_tokens[-1]:<12,d} | {rdl_tokens[-1]:<12,d}")
    reduction_vs_llm = (1.0 - (rdl_tokens[-1] / llm_tokens[-1])) * 100.0
    reduction_vs_rag = (1.0 - (rdl_tokens[-1] / rag_tokens[-1])) * 100.0
    print(f"{'Reduction vs Baseline':<30} | {'0.0%':<12} | {'--':<12} | {f'{reduction_vs_llm:.1f}% vs LLM':<12}")
    print(f"{'': <30} | {'':<12} | {'0.0%':<12} | {f'{reduction_vs_rag:.1f}% vs RAG':<12}")

    tier_counts = {0: 0, 1: 0, 2: 0, 3: 0}
    for t in rdl_tiers:
        tier_counts[t] = tier_counts.get(t, 0) + 1

    print("-" * 70)
    print("RDL Enterprise Tier Distribution (Inverse Scaling Progression):")
    for t in sorted(tier_counts.keys()):
        pct = (tier_counts[t] / total_tickets) * 100.0
        print(f"  Tier {t} ({'Exact Cache' if t==0 else 'Local Rule' if t==1 else 'Local Vector' if t==2 else 'External LLM'}): {tier_counts[t]:2d} tickets ({pct:.1f}%)")

    first_10_rdl = sum(rdl_tokens[i] - (rdl_tokens[i-1] if i > 0 else 0) for i in range(10))
    last_10_rdl = sum(rdl_tokens[i] - rdl_tokens[i-1] for i in range(50, 60))
    first_10_llm = 10 * TIER_COST_MAP[3]
    last_10_llm = 10 * TIER_COST_MAP[3]

    print("\nMetabolic Learning Curve (Tickets 1-10 vs 51-60):")
    print(f"  First 10 tickets cost : RDL = {first_10_rdl:,} tokens  vs  LLM = {first_10_llm:,} tokens")
    print(f"  Last 10 tickets cost  : RDL = {last_10_rdl:,} tokens  vs  LLM = {last_10_llm:,} tokens")
    print(f"  Inverse Scaling Factor: Last 10 tickets consumed {((last_10_rdl / max(first_10_rdl, 1))) * 100.0:.1f}% tokens of the initial phase.")
    print("=" * 70)

if __name__ == "__main__":
    run_benchmark()
