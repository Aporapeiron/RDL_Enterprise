# RDL_Enterprise — RDL業務AI ランタイム

**RDL（関係力学言語 / Relational Dynamics Language）** をベースにした、自律適応型業務AIエージェントの有限条件下検証・参照実装。

既存のLLMを「AIの本体」ではなく「外部推論器・未回収関係（$\xi$）に対する探索候補生成器」と位置づけ、業務経験を通じて職場固有の有限関係拘束構造 **$M_B$** を形成・沈澱させ、**「仕事に慣れるほど計算量・コストが逓減する（逆スケーリング）」** 閉じた代謝ループを実現します。

RDLの実装時に認識論的境界を維持する規律は、[RDL Coding Principles](docs/RDL_Coding_Principles.md) に定義します。Operational Lexiconが用語の意味境界を定めるのに対し、本書は観測・Commitment・外生条件・Evidence polarity・Replay・テスト解釈をコードへ落とす際の規則を定めます。

Core抽出時の受入条件と候補分類は、[RDL Core Extraction Gate](docs/RDL_Core_Extraction_Gate.md) に定義します。

Enterprise `MBNode` のfield分類とCore投影範囲は、[MBNode Field Projection Map](docs/RDL_MBNode_Field_Projection.md) に定義します。

`trigger_pattern` の意味分類とCore昇格条件は、[RDL Trigger Semantics](docs/RDL_Trigger_Semantics.md) に定義します。

FunctionとCompiled M_Bの関係、および再コンパイル境界は、[RDL Compiled M_B](docs/RDL_Compiled_MB.md) に定義します。

本リポジトリにおける「一致」「再現」「十分性」「閉包」は、明示または暗黙に設定された有限境界 $B$、問い $Q$、時点 $t$、観測断面 $O$、運用目的 $P$ に対する性質です。終端的完全性・世界そのものの決定論性・絶対的真理性・絶対的安全性を意味せず、いかなる運用閉包においても $\xi$ は残存します。RDL内部では、真理・現実そのもの・絶対的正解を直接認証せず、支持・対向・権威・来歴・拘束強度・運用採用として有限記述します。

---

## 🧭 コア特徴

1. **閉じた代謝ループ（Metabolic Closed Loop）**:
   * 未知案件は Tier 3（外部LLM）または Tier 1（確定規則）で推論 $\to$ ユーザー解決等の成功確認（$EFP'$ 観測）を経て、ライブ実行系の **Level 0 キャッシュおよび $M_B$ へ沈澱（Sedimentation）** $\to$ 次回同一・類似案件は Tier 0（ローカル・ゼロコスト）で即答。
2. **権限境界と権威的方針注入（Authority Separation）**:
   * 人間の管理権限・方針注入（origin="authority"）と、業務成功に伴う経験沈澱（origin="experience"）を系統分離し、自己例外化（B5）を防止。
3. **動的アクティブ制約部分グラフ（Active Constraint Subgraph）**:
   * ドメイン関連度・関係性伝播・ノード種別（組織ポリシー、法令、手続き）に基づき、案件ごとに必要な制約部分グラフを抽出。
4. **凍結解釈文脈（FrozenInterpretationContext）と ReplayToken**:
   * 意思決定時のコンテキストハッシュ $h_{ctx}$ とトークン $\tau_{replay}$ を、条件拘束再演のための有限証跡として記録。監査・差分検証・反実仮想（Counterfactual Simulation）の境界内検証成立を担保。
5. **自己修正力低下（$\kappa \to 0$）と HITL（Human-in-the-Loop）**:
   * 確信度不足や破断兆候を力学的に検出し、自律的に人間に質問・確認。先輩の回答を成功確認後に $M_B$ へ沈澱（権限者による方針指示は即時反映）。
6. **環境変化の検知と安全な再編相（$M_\Delta$）**:
   * 制度変更や組織改編で従来の予測が外れると不整合熱 $H$ が蓄積し、有効判定境界 $\theta_{eff} = \theta_0 - g(\xi_{obs})$ を突破して自動的に再編相 $M_\Delta$ へ突入。
   * 耐久テストハーネス（回帰・権限境界・表現揺らし）、シャドウ推論（Shadow Execution）、バージョン隔離されたCanary運用（Canary Isolation）を経て、現在の Authority/Policy と検査閾値に束縛された本番適用（Leap）へ進む。

---

### 📂 ディレクトリ構成

```text
RDL_Enterprise/
├── docs/
│   ├── RDL業務AI_詳細設計書_v1.0.md    # 初期アーキテクチャ設計書
│   └── RDL業務AI_詳細設計書_v2.0.md    # v2.0 統合詳細設計書（代謝閉ループ・制約部分グラフ・権威分離・監査）
├── data/
│   ├── seed_it_support.json             # 社内ITサポート初期シードグラフ
│   └── social_fixtures_sample.json      # 耐久ハーネス用ソーシャル摂動データ
├── src/
│   ├── rdl_enterprise/
│   │   ├── __init__.py                  # パッケージ公開API
│   │   ├── authority.py                 # AuthorityContext（権限境界・役職定義）
│   │   ├── canary.py                    # CanaryManager, CanaryPolicy（Canaryデプロイ・熱隔離・自動ロールバック）
│   │   ├── cascade.py                   # InterpCascade（Level 0〜3 多層推論・バージョン束縛キャッシュ・沈澱）
│   │   ├── constraint.py                # ConstraintEngine, RuleNode, Locus, Active Constraint Subgraph
│   │   ├── durability.py                # DurabilityHarness（回帰・境界・摂動破断チェッカー）
│   │   ├── h_state.py                   # HState（熱ベクトル・自然散逸・θ_eff判定・バージョン別熱管理）
│   │   ├── human.py                     # HumanQuery（κゲート・HITL制御）
│   │   ├── mb_graph.py                  # MBNode, MBGraph（慣性質量・κ・バージョン管理・JSON永続化）
│   │   ├── promotion_gate.py            # PromotionGate, ShadowEvaluator（シャドウ反実仮想評価・昇格判定）
│   │   ├── runtime.py                   # EnterpriseRuntime（代謝オーケストレーション・非同期ライフサイクル）
│   │   ├── scenarios/                   # Enterprise シナリオパック（60日ライフサイクル、権威管轄衝突、摂動ストレス）
│   │   ├── shadow.py                    # ShadowExecutionRunner, CounterfactualComparator（シャドウ並行推論）
│   │   ├── simulation_adapter.py        # SimulationWorld と EnterpriseRuntime の双方向ブリッジ
│   │   ├── snapshot.py                  # CaseSnapshot, FrozenInterpretationContext, ReplayToken
│   │   └── social_adapter.py            # SocialFixtureAdapter
│   └── rdl_simulation/                  # 【汎用】RDL Simulation Harness（Core/Game共用テスト基盤）
│       ├── __init__.py                  # シミュレーションAPI
│       ├── agent.py                     # SimAgent, Persona（有限観測生成）, UserAgent, AuthorityAgent, EnvironmentAgent
│       ├── clock.py                     # SimulationClock（離散Tick / 仮想日時同期）
│       ├── events.py                    # SimEvent, EventQueue（優先度付き時系列キュー）
│       ├── metrics.py                   # SimMetricsCollector（全体統計 & コホート別局所破断追跡）
│       ├── replay.py                    # SimTraceLogger（イベントトレース / 条件固定再現）
│       ├── scenario.py                  # ScenarioPack, ScenarioEvent（シナリオ定義基底）
│       └── world.py                     # SimulationWorld（実行統合体）
├── tests/
│   ├── test_canary.py                   # Canary隔離・熱監視テスト
│   ├── test_candidate_immutability.py   # 候補ノード不変性・スナップショットテスト
│   ├── test_constraint_model.py         # 制約部分グラフ・関係伝播・Locus意味付けテスト
│   ├── test_core.py                     # コア代謝・ライフサイクルテスト
│   ├── test_product_acceptance.py       # 製品受入テスト（最小代謝閉ループ・権威分離・バージョン束縛・観測保留・鮮度分離・証拠極性分離・ライフサイクルコミット等 14大テスト）
│   ├── test_promotion_gate.py           # 昇格ゲート・シャドウ評価テスト
│   ├── test_shadow.py                   # 反実仮想シャドウ推論テスト
│   ├── test_property_invariants.py      # 多変量プロパティベース不変条件テスト（UNKNOWN純粋性・OPPOSE非更新・権威遮断等）
│   ├── test_simulation_harness.py       # シミュレーションハーネス単体・結合テスト
│   ├── test_simulation_scenarios.py     # 長期シナリオ・権威衝突・条件固定再現・摂動受入アサーションテスト
│   └── test_social_adapter.py           # ソーシャル摂動フィクスチャテスト
├── run_simulation.py                    # 5大有限条件下検証シナリオ実行スクリプト
├── run_multiagent_sim.py                # 長期・複数主体・イベント駆動シミュレーション実行スクリプト
├── benchmark_cost_curve.py              # 合成トークン等価シミュレーションベンチマーク（LLM vs RAG vs RDL）
├── pyproject.toml
└── README.md
```

---

## 🚀 クイックスタート

外部依存ライブラリなし（Python 3.9+ 標準ライブラリのみ）で動作します。

### 1. 5大シナリオ有限条件下検証シミュレーションの実行

```bash
# Windows
py run_simulation.py

# macOS / Linux
python3 run_simulation.py
```

* **シナリオ1**: パスワードリセットの反復によるコスト沈澱（Tier 1 $\to$ Tier 0 キャッシュ化）
* **シナリオ2**: VPN接続障害での失敗 $\to$ 人間からの暗黙知獲得 $\to$ 次回自律解決
* **シナリオ3**: 社内ツールの移行に伴う発熱（$H \ge \theta_{eff}$）$\to$ 耐久検査・シャドウ推論を経て再編相 $M_\Delta$ Leap
* **シナリオ4**: 業務AIの非同期ライフサイクル（保留 PENDING $\to$ 翌朝フィードバック回収 SUCCESS）
* **シナリオ5**: 案件放置によるタイムアウト（UNKNOWN化 $\to$ 不確実性熱の蓄積）

### 2. 長期・複数主体・イベント駆動シミュレーションの実行（RDL Simulation Harness）

```bash
# 60日間の長期ライフサイクル（平常沈澱 → 制度変更 → 発熱 → 破断 → MΔ → Shadow → Leap）
py run_multiagent_sim.py --scenario lifecycle --days 60

# 権威境界・管轄衝突（一般社員の伝聞 vs セキュリティ責任者の正式指示 vs 経理の越境介入遮断）
py run_multiagent_sim.py --scenario authority

# 過酷な摂動ストレス（表記揺れ・急増する放置・成功/失敗の交互連続・コホート別局所破断評価）
py run_multiagent_sim.py --scenario stress --days 15
```

### 3. コストカーブ・逆スケーリングのベンチマーク実行

```bash
py benchmark_cost_curve.py
```

Pure LLM（毎回フルプロンプト推論）、Standard RAG（毎回検索注入）、RDL Enterprise（成功体験の沈澱による Tier 0 化）における合成トークン等価消費量とコスト削減率を比較計測します。

### 4. テストスイートの実行

```bash
py -m pytest -o pythonpath=src
# または
py -m unittest discover tests
```

全 **164件** の単体・結合・受入・ジェネレーティブ不変条件テスト（時間注入・条件固定再現Replay・60日ライフサイクル検証・Fail-Closed Replay検証・AIコア遷移関連状態ダイジェスト照合を含む）が高速（約5.6秒）にパスします。
