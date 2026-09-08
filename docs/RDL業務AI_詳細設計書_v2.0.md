# RDL業務AI 詳細設計書 v2.0
*文書コード：RDL-ENT-SPEC-02 / 統合実装仕様書*  
*準拠公理：RDL T0 基底措定（BASE v2.0） / T0 最低動作仕様（SPEC v2.0） / T1 操作仕様（SILN Operations）*

---

## 1. システム概要と基本思想

### 1.1 背景と設計目標
本システム（`RDL_Enterprise`）は、既存の大規模言語モデル（LLM）や検索基盤を「AIの本体」として特権化せず、高負荷時や未知探索時に起動される「外部推論器・未回収関係（$\xi$）展開器」と位置づける。
業務経験を通じて、その組織固有の有限関係拘束構造 **$M_B$（自己側の有限整合構造）** を形成・適応させ、**「仕事に慣れるほど計算量・コストが逓減する（逆スケーリング）」** 閉じた代謝ループを実現する自律型業務AIランタイムである。

```text
【目標像】
「新入社員として配属され、経験と人との対話を通じてベテランへと成長し、
  制度改変には自律的に発熱して再編・適応するAI」
```

### 1.2 RDL T0/BASE v2.0 への厳格準拠
1. **有限境界 $B$ と未回収関係 $\xi$**:
   * 業務ドメイン（アカウント、ネットワーク、ハードウェア等）、組織権限、アクセス可能ツールを明示的な境界 $B$ として画定する。
   * 境界 $B$ を引く限り、制度の隙間や文脈依存の例外（$\xi$）が不可避に残る。本システムは全知全能を仮定せず、常に未回収関係 $\xi$ を力学的に追跡・計測する。
2. **代謝ループの閉塞（Closed Loop）**:
   * 外部入力 $EFP$ に対する予測 $F$ の出力にとどまらず、事後結果 $EFP'$ の観測、内部解釈差分 $E$ の算出、熱 $H$ の蓄積／散逸、そして **成功案件（`CaseStatus.SUCCESS`）の $M_B$ およびライブ Level 0 キャッシュへの沈澱（Sedimentation）** までを一貫した閉ループとして成立させる。
3. **自己例外化禁止（B5公理）**:
   * AI自身の権限判定ルール、HITL（人に聞く）閾値、ツール実行規則も $M_B$ のノード群および制約構造の中に組み込まれ、例外的な特権を持たず、再編相 $M_\Delta$ の検査・改訂対象となる。
4. **権威的方針注入（Authority Injection）と経験沈澱（Experience Sedimentation）の分離**:
   * 人間管理者による明示的な方針変更（`origin="authority"`）と、現場業務の成功に伴う経験沈澱（`origin="experience"`）を系統（Lineage）として厳密に分離し、権威昇格や監査の正当性を保つ。

---

## 2. 全体アーキテクチャ

システムは、代謝骨格を司る **RDL Runtime Core**、階層型推論を行う **InterpCascade（推論関数スロット）**、動的制約を解決する **ConstraintEngine**、再編時の安全性を保証する **DurabilityHarness ＆ PromotionGate** から構成される。

```mermaid
graph TD
    UserIn[業務入力 EFP] --> Runtime[EnterpriseRuntime Core]
    
    subgraph Core[RDL Runtime Core]
        ContextMgr[Frozen Context & ReplayToken]
        HState[HState 熱管理・自然散逸・θ_eff判定]
        CanaryMgr[CanaryManager バージョン熱隔離]
        Lifecycle[非同期ライフサイクル PENDING/SUCCESS/UNKNOWN]
    end

    subgraph Inference[InterpCascade 推論多層スロット]
        L0[Tier 0: Level 0 バージョン束縛キャッシュ]
        L1[Tier 1: Level 1 構造化確定ルール]
        L2[Tier 2: Level 2 局所文字N-gram類似検索 (bi-gram Jaccard)]
        L3[Tier 3: Level 3 外部LLM推論器]
    end

    subgraph Constraints[ConstraintEngine & Active Subgraph]
        LocusStage[Locus Semantic Staging]
        ActiveSub[Active Constraint Subgraph 抽出]
        RelProp[1-hop 関係性伝播 & 意味制約検証]
    end

    subgraph Durability[Durability & Promotion Gate]
        Harness[DurabilityHarness 破断検査]
        ShadowRunner[ShadowExecutionRunner 反実仮想並行推論]
        Gate[PromotionGate 自動ロールバック & Leap]
    end

    Runtime --> Inference
    Inference <--> Constraints
    Inference --> Action[予測出力 F / アクション実行]
    Action --> Feedback[事後結果 EFP' / 人間フィードバック]
    Feedback --> Runtime

    Runtime -- "成功確認 (SUCCESS)" --> Sediment[Level 0 & M_B 沈澱]
    Sediment --> L0
    
    Runtime -- "H >= θ_eff (再編発動)" --> Durability
    Durability -- "合格 M_B' (Leap)" --> Runtime
```

---

## 3. 主要コンポーネント詳細仕様

### 3.1 凍結解釈文脈（FrozenInterpretationContext）と ReplayToken
推論時の境界 $B$ と $M_B$ の状態を改ざん不能な確定スナップショットとして固定する。
* **`context_hash` ($C_0$)**: 推論時の解釈前提条件を決定論的に固定する暗号論的ハッシュ (SHA-256)。以下の8フィールドから構成される:
  1. `mb_version`: $M_B$ のバージョン識別子
  2. `mb_content_hash`: $M_B$ グラフ内容の暗号論的ハッシュ
  3. `target_domain`: 案件の対象ドメイン・業務境界 $B$
  4. `cascade_config`: 推論カスケード動作設定
  5. `llm_identity`: 外部推論器の固有アイデンティティ
  6. `cache`: Level 0 キャッシュの決定論的ソート済みシリアライズ
  7. `constraint_config`: 関係拘束評価パラメーター設定
  8. `constraint_evaluation_time`: dispatch 時に凍結された関係拘束評価時刻 (ISO-8601)
* **`ReplayToken` ($K$)**: 案件ID、タイムスタンプ、入力特徴、および $C_0$ から導出される一意トークン。事後フィードバック時やシャドウ並行推論時の反実仮想比較（Counterfactual Comparison）における基準線となる。

### 3.2 動的アクティブ制約部分グラフ（Active Constraint Subgraph）
すべてのルールノードを全件照合するのではなく、案件の文脈・ドメイン・権限境界に応じて動的に部分グラフを切り出す。
1. **Locus Semantic Staging**:
   * ノード群を役割（`core` / `auxiliary`）および意味論的座（Locus: 組織方針、法務制約、業務手順、例外措置）へステージング。
2. **グラフ活性化と 1-hop 伝播**:
   * 入力適合度が閾値（`relevance_floor = 0.05`）以上のシードノードから、明示的拘束エッジ（`support`, `authority`, `policy_authority`）、共起関係（`co_occurs`）、および補助的推定関係（`inferred_support`）を持つ隣接ノードを 1-hop 伝播して活性化部分グラフ $L_{candidate}$ を構成。
3. **Applied View Contract**:
   * 実際に推論・評価プロセスを通っていない制約の「見せかけの適用（捏造）」を型レベルで遮断し、`execution_trace` に基づく追跡可能な制約のみを `applied_constraints` として記録。

### 3.3 最小代謝ループの閉塞（Closed Loop Sedimentation）
* **Level 0 キャッシュの厳格なバージョン束縛**:
  * キャッシュキーは `(mb_version, domain, normalized_query)` の3組で構造的に束縛される。
  * グラフ更新（Leap や Rollback）によって `mb_version` が更新された場合、過去バージョンのキャッシュが誤適用される事故をゼロにする。
  * 旧形式キャッシュの取り込みは、明示的な `migrate_legacy_cache(cache, source_mb_version)` 経由でのみ許可され、由来バージョン（provenance）なしでの自己昇格を禁止。
* **沈澱（Sedimentation）と観測保留（UNKNOWN ≠ FAILURE）の契約**:
  * 案件受付時（未確認時）にはライブ Level 0 キャッシュへ書き込まず、非同期ライフサイクルを経て `user_resolved == True` かつ `!human_rejected` が確認された段階で初めて `sediment_level0()` を呼び出す。
  * タイムアウトや未回収案件（UNKNOWN）は判断の誤り（FAILURE）ではなく未確定な未回収関係（$\xi$）の残存として扱い、ノード統計（`failure_count` / `confidence`）を悪化させず `unresolved_count` として独立記録。
* **意味的証拠鮮度（`last_evidence_at`）と観測残差（`last_observed_at`）の直交分離**:
  * 関係拘束スコア（`freshness`）の計算元には、肯定的・確定的な経験更新（成功・失敗・方針注入・結晶化）のタイムスタンプである `last_evidence_at` のみを用いる。
  * タイムアウト等の観測不能（UNKNOWN）は観測時刻 `last_observed_at` のみを更新し、`last_evidence_at` は保存される（「未確認放置案件による不当な鮮度リフレッシュ」の完全遮断）。
  * グラフ同一性（`content_hash`）には行動力学・時間拘束に直結する `last_evidence_at` を包含し、過渡的観測残差 $\xi$ である `last_observed_at` および `unresolved_count` は除外する。
* **証拠極性の分離（Evidence Polarity Separation: 支持 vs 反証）**:
  * 証拠タイムスタンプを肯定的支持証拠（`last_support_at`）と否定的反証証拠（`last_opposing_at`）に分離。
  * 失敗・差し戻し（FAILURE / REJECTED）は `last_opposing_at` を更新し、`last_support_at` は保存されるため、失敗によって既存拘束 $C_{rel}$ の支持鮮度（`freshness`）が不当に上昇する逆転現象を根絶。
  * **厳格なフォールバック排除（Strict Polarity Isolation）**: `constraint.py` における支持鮮度（`freshness`）の計算は、すべて `last_support_at` のみから算出され、`last_updated` への暗黙フォールバック（対向のみノードが `last_opposing_at` 経由で支持鮮度を得る抜け穴）を完全に排除。`last_support_at` が未指定（`None`）のノードは即座に `freshness = 0.0` として計算される。
  * **フェイルクローズなレガシー移行 & セッター遮断**: 旧データ取り込み時、実績ゼロ（`success=0, failure=0`）のノードに対して勝手に `last_support_at` を捏造することを禁止（公理 B5）。起源不明のタイムスタンプは `legacy_evidence_at`（$\xi$）として保持し、支持鮮度には寄与させない。またレガシーセッター（`@last_evidence_at.setter`, `@last_updated.setter`）は `AttributeError` を送出する読み取り専用プロパティとし、極性曖昧な代入を型・契約レベルで遮断。
  * **設定化された反証破断検査（Rupture Probe Thresholds）**: 過去の反証実績は $M_B$ 内部の「歴史的反証拘束シグナル（`historical_opposing_signal`）」として蓄積・計算され、`ConstraintConfig` の明示パラメータ（`rupture_opposing_freshness_threshold: 0.7`, `rupture_opposing_signal_threshold: 1.2`, `rupture_opposing_support_freshness_cap: 0.5`）に基づいて破断検査（`RuptureProbe`）の内部亀裂判定に用いられる。
  * ※ 外界後続入力 $EFP'$ の拘束 $C'$ と、過去の反証履歴（$M_B$ 状態）は厳格に分離され、$C'$ に歴史的反証シグナルを混同しない（SPEC公理整合）。

### 3.4 権威的方針注入（Authority Injection）の分離
* `InterpCascade.crystallize_rule()`: 業務成功から自律生成される経験的ルール（`origin="experience"`, `source_lineage="sedimentation:experience"`）。
* `InterpCascade.inject_authoritative_rule()`: 権限者（管理者、情報セキュリティ責任者等）から明示的に付与される方針ノード（`authority_level="policy"`, `source_lineage="authority:{role}:{actor}"`）。
* ガバナンスにおいて、経験的ノードは耐性低下や発熱によって淘汰・再編され得るが、権威的ノードは権限者の明示的な改廃手続きを経るまで保持される。

### 3.5 バージョン隔離された Canary 運用と自動ロールバック
* **Canary 熱隔離**:
  * Canary 展開中のバージョンにおいて発生した不整合・タイムアウト熱は、本番（Production）の `HState` を一切汚染せず、`CanaryManager` 固有の熱状態に蓄積される。
* **自動ロールバック**:
  * Canary 熱が閾値 $\theta_{canary}$ を超過した場合、またはシャドウ反実仮想評価で改悪率が許容限界を超えた場合、即座に本番バージョンへ安全にロールバックされる。

---

## 4. 計算力学モデル（T0 Primitive 実装）

### 4.1 関係拘束抵抗断面 $I(M_B)$ と自己修正可能性 $\kappa$
各ノード $n$ の更新抵抗断面（慣性質量） $I(n)$ および自己修正可能性 $\kappa(n)$ は、経験の蓄積（成功 $S$, 承認 $A$, 失敗 $F$, 却下 $R$）により更新される（BASE v2.0 §4.2 / SPEC v2.0 §6.2）。

$$
I(n) = \max\left(0.0, \text{confidence} \times (1.0 + 0.3 S + 0.5 A - 0.5 F - 0.8 R)\right)
$$

$$
\kappa(n) = \exp\left(-\frac{I(n)}{M_0}\right) \quad (M_0 = 3.0)
$$

* 経験を重ねて安定したノードは $I(n)$ が増大し、$\kappa \to 0$（硬化・低コスト即答）。
* 失敗や差し戻しが重なると $I(n)$ が減衰し、$\kappa$ が上昇（軟化・再編容易化）。
* ※ $I(n)$ は「更新抵抗断面」であり、確信度ブーストには関係拘束スコア $C_{rel}$ を使用する。

### 4.2 熱 $H$ の多層追跡と動的有効閾値 $\theta_{eff}$
熱 $H$ は予測誤差成分と入力補助成分の合成ベクトル `HeatVector(prediction, input_err)` として追跡される。

$$
H_{total} = w_{pred} \cdot H_{pred} + w_{input} \cdot H_{input} \quad (w_{pred} = 1.0, w_{input} = 0.4)
$$

強い関係拘束と衝突した不整合は大きく保持され（$H \leftarrow H + E \times C_{opposing}$）、未回収関係（観測された外部ノイズ・文脈不確実性）$\xi_{obs}$ に応じて、有効閾値 $\theta_{eff}$ は動的に引き下げられる。

$$
\theta_{eff} = \theta_0 - g(\xi_{obs})
$$

$H_{total} \ge \theta_{eff}$ に達した瞬間、巡航相（Cruise）から再編相（$M_\Delta$）への状態遷移が強制発火する。

---

## 5. 検証済み受入テスト基準（Phase A Acceptance Matrix）

| テスト識別子 | 検証対象メカニズム | 合格基準 |
|---|---|---|
| **Test 1** | 最小代謝閉ループ | Tier 1/3 応答後、`FeedbackResult(user_resolved=True)` の回収を経て即座に Tier 0 キャッシュへ沈澱し、次回同一クエリが Tier 0 で即答されること。 |
| **Test 2** | 未解決案件の汚染防止 | `user_resolved=False` または人間差し戻し案件は Level 0 キャッシュへ沈澱しないこと。 |
| **Test 3** | 未知案件の HITL 発火 | 確信度不足の未知案件に対し、人間に確認（`hitl_triggered=True`）し、回答が $M_B$ へ沈澱すること。 |
| **Test 4** | 環境変化と $M_\Delta$ 発火 | 苦情・失敗の連続により $H \ge \theta_{eff}$ となり、再編相プロポーザルが起草されること。 |
| **Test 5** | 耐久ハーネス検証 | 回帰検査・権限境界検査・摂動ストレステストを通過した候補のみが昇格対象となること。 |
| **Test 6** | Canary タイムアウト熱隔離 | Canary スナップショットのタイムアウト発生時、本番 `HState` の熱および $\theta_{eff}$ が一切影響を受けず、Canary 側のみ発熱・ロールバック判定されること。 |
| **Test 7** | 権威方針と経験沈澱の分離 | `origin="authority"` で注入されたノードは `authority_level="policy"` および固有 Lineage を保持し、経験沈澱ノードと混同されないこと。 |
| **Test 8** | バージョン束縛キャッシュ | グラフバージョン更新後、旧バージョンのキャッシュキーが無効化され、旧compatキーが存在していても新バージョン側で誤適用されず厳格に推論が再実行されること。 |
| **Test 9** | 未認可方針注入の遮断 | 権限範囲外のアクターによる `inject_authoritative_rule()` の呼び出しが `PermissionError` で即座にフェイルクローズ遮断されること。 |
| **Test 10** | TIMEOUTの観測保留 | タイムアウト案件（UNKNOWN）において、ノードの `failure_count` や `confidence` が悪化せず、`unresolved_count` として観測保留記録されること。 |
| **Test 11** | キャッシュ移行の起源明示 | 旧形式キャッシュのインポート時に `source_mb_version` の明示を義務付け、現行バージョンへの不当な自己昇格が防止されること。 |
| **Test 12** | 意味的鮮度と残差の分離 | タイムアウト案件（UNKNOWN）において、ノードの `last_evidence_at` および `content_hash` が保存され、不当な鮮度リフレッシュが発生しないこと。 |
| **Test 13** | 証拠極性分離と反証シグナル | 失敗・差し戻し発生時に `last_opposing_at` が更新され、`last_support_at` は保存されて支持鮮度の上昇が防止されること。支持鮮度は `last_support_at` のみから算出され対向のみノードで 0.0 となること、レガシーセッターへの代入が `AttributeError` となること、実績ゼロのレガシーノードで極性捏造を行わないこと、歴史的反証シグナルが破断検査に反映され $C'$ と分離されること。 |

---

## 6. 結論と次期フェーズ展望

本文書で定義された `RDL_Enterprise v2.0` は、現時点の有限実装境界において、T0/BASE の主要契約（有限境界 $B$、未回収関係 $\xi$ の残存、代謝閉ループ、自己例外化禁止）を実装・検証した。
経験の蓄積に伴う計算コスト逓減（逆スケーリング）はシミュレーション上で実証され、Canary 熱隔離、権威方針分離、および観測保留（UNKNOWN ≠ FAILURE）規律によってエンタープライズ実証に耐えうる検証基盤を確立した。

次期フェーズ（Phase B）では、実業務データ連携、非同期分散キュー統合、およびマルチエージェント間の境界調停へと展開を進める。
