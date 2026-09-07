# RDL_Enterprise — RDL業務AI ランタイム

**RDL（関係力学言語 / Relational Dynamics Language）** をベースにした、自律適応型業務AIエージェントの参照実装。

既存のLLMを「本体」ではなく「外部推論器」と位置づけ、業務経験を通じて職場固有の判断構造 **$M_B$** を形成・沈澱させ、**「仕事に慣れるほど計算量・コストが逓減する」** 逆スケーリングを実現します。

---

## 🧭 特徴

1. **新入社員からベテランへ**:
   * 未知案件は高コストな外部LLMで推論 $\to$ 成功した判断をルールや局所モデル（$M_B$）へ沈澱 $\to$ 次回以降はローカル最小コストで即答。
2. **人に聞く（Human-in-the-Loop）**:
   * 確信度不足や自己修正力低下（$\kappa \to 0$）を力学的に検出し、自律的に人間に質問・確認。
3. **環境変化の検知と再編（$M_\Delta$）**:
   * 制度変更や組織改編で従来の予測が外れると熱 $H$ が蓄積し、有効判定境界 $\theta_{eff} = \theta_0 - g(\xi_{obs})$ を突破して自動的に再編相 $M_\Delta$ へ突入。

---

## 📂 ディレクトリ構成

```text
RDL_Enterprise/
├── docs/
│   └── RDL業務AI_詳細設計書_v1.0.md    # 統合仕様書（v1.0 固定済）
├── data/
│   └── seed_it_support.json             # 社内ITサポート初期シード
├── src/
│   └── rdl_enterprise/
│       ├── mb_graph.py                  # MBNode, MBGraph（慣性質量・κ・JSON永続化）
│       ├── snapshot.py                  # CaseSnapshot（非同期F/F'差分・結果追跡）
│       ├── h_state.py                   # HState（熱ベクトル・散逸・θ_eff判定）
│       ├── cascade.py                   # InterpCascade（Level 0〜3 多層推論）
│       ├── human.py                     # HumanQuery（κゲート・HITL制御）
│       └── runtime.py                   # EnterpriseRuntime（巡航代謝・非同期ライフサイクル）
├── tests/
│   └── test_core.py                     # 単体テストスイート
├── run_simulation.py                    # 3大シナリオ実証スクリプト
├── pyproject.toml
└── .gitignore
```

---

## 🚀 クイックスタート

外部依存ライブラリなし（Python 3.9+ 標準ライブラリのみ）で動作します。

### 1. 3大シナリオ実証シミュレーションの実行

```bash
# Windows
py run_simulation.py

# macOS / Linux
python3 run_simulation.py
```

* **シナリオ1**: パスワードリセットの反復によるコスト沈澱（Level 1 $\to$ Level 0 キャッシュ化）
* **シナリオ2**: VPN接続障害での失敗 $\to$ 人間からの暗黙知獲得 $\to$ 次回自律解決
* **シナリオ3**: 社内ツールの移行に伴う発熱（$H \ge \theta_{eff}$）と再編相 $M_\Delta$ 発動

### 2. 単体テストの実行

```bash
py -m unittest discover -s tests -p "test_*.py"
```
