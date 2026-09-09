# RDL業務AI 詳細設計書 v2.0
*文書コード：RDL-ENT-SPEC-02 / 統合実装仕様書*  
*準拠公理：RDL T0 基底措定（BASE v2.0） / T0 最低動作仕様（SPEC v2.0） / T1 操作仕様（SILN Operations）*

---

## 0. 運用語彙規約（Operational Lexicon）

実装時の規律は、用語規約とは分離して [RDL Coding Principles](RDL_Coding_Principles.md) に定義する。Operational Lexiconが「何を意味するか」を定めるのに対し、Coding Principlesは「その意味を壊さないためにコードが何をしてはいけないか」を定める。

本仕様における「一致」「再現」「十分性」「閉包」「検証成立」は、明示または暗黙に設定された有限境界 $B$、問い $Q$、時点 $t$、観測断面 $O$、および運用目的 $P$ に対する性質であり、終端的完全性・世界そのものの決定論性・絶対的真理性・絶対的安全性を意味しない。いかなる運用閉包においても $\xi$ は残存する。

強い述語 $P$ を用いる場合は、その成立域となる有限関係条件を回収可能にする。

```text
P
↓
P(B, Q, t, O, Purpose)

P(B, ...)
does not eliminate ξ(B)
```

| 従来語 | 本仕様での意味語 | 運用上の読み |
|---|---|---|
| 完全 | 運用閉包 / 境界内閉包 | 現在の $B/Q/t/O/P$ で作用継続に必要な関係が一旦閉じている |
| 完全状態 | 遷移関連状態 | 現在定義した遷移・観測・再演に影響すると扱う有限状態 |
| future-equivalent | 遷移境界内同値 | 現在採用した遷移観測境界では区別されない |
| 決定論的 | 条件固定再現性 / 再現安定性 | 固定した外生条件下で同じ観測系列が再現する |
| 完全一致 | 境界内同値 / 観測同値 | 指定された比較断面で同値 |
| true replay | 条件拘束再演 / 境界再演 | 指定 RunContext のもとで再演する |
| 最終状態 | 観測終了時状態 | 指定した観測区間の終了時点における遷移関連状態 |
| 証明 | 境界内検証成立 | $B$ 内で要求した検査が成立した |
| 成功 | 局所安定 / 運用成立 | 現在の有限観測で期待した応答関係が成立した |
| 必要 | 境界内必要性 | 現在の $B/Q/t/Purpose$ で操作成立に必要と扱う |
| 十分 | 運用十分性 | 現在の $B/Q/t/Purpose$ で追加探索なしに作用可能と扱う |
| 安全 | Authority/Policy に束縛された許容リスク内 | 指定された $B/Q/t/O/Purpose$、権限主体、損失関数、閾値のもとで破断条件を超えていない |
| 保証 | 契約上の遮断 / 境界内検証成立 | 指定された実装契約・検査境界で逸脱経路を遮断する |
| 実証 | 有限条件下検証 / 運用観測 | 指定条件のシナリオまたは運用観測で成立を確認した |
| 正解 | 現在 $B/Q/t/Purpose$ で運用採用された解釈 | 世界そのものの正解ではなく、現在境界内で採用される $F$ |
| 真実 / 真理性 | 内部確定しない外部述語 | 世界そのものの真理状態へ直接写像せず、支持・対向・権威・来歴・拘束強度として有限記述する |

実装識別子としての `exact`、`content_hash`、`deterministic_replay`、`SUCCESS` などは、ビット列・データ構造・API状態ラベルとして保持する。ただし、それらの結果をRDL意味層で読む際は、常に上記の有限化された意味へ写像する。

### 0.1 内部述語と外部保留述語

RDL内部で扱う述語は、関係・拘束・支持・対向・Commitment・provenance・authority・threshold・$F/F'/E/H/\xi$・局所安定・運用十分性・境界内同値である。これらは有限境界内での観測・採用・遮断・更新を記述するための内部語彙であり、世界そのものの真理状態を確定するものではない。

RDLは内部状態として、Truth、Reality itself、absolute correctness、absolute safety、completeness を直接認証しない。これらの語を運用文書で参照する場合は、上表のように成立境界・権限主体・観測断面・閾値・残存する $\xi$ を回収可能な形へ移す。

## 1. システム概要と基本思想

### 1.1 背景と設計目標
本システム（`RDL_Enterprise`）は、既存の大規模言語モデル（LLM）や検索基盤を「AIの本体」として特権化せず、高負荷時や未知探索時に起動される「外部推論器・未回収関係（$\xi$）に対する探索候補生成器」と位置づける。LLM出力は $\xi$ の解消そのものではなく、検査・選別・コミットメントを待つ候補関係材料として扱う。
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

システムは、代謝骨格を司る **RDL Runtime Core**、階層型推論を行う **InterpCascade（推論関数スロット）**、動的制約を解決する **ConstraintEngine**、再編時に現在の検査境界で昇格条件を満たすかを検証する **DurabilityHarness ＆ PromotionGate** から構成される。

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
推論時の境界 $B$ と $M_B$ の遷移関連状態を、条件拘束再演のための確定スナップショットとして固定する。
* **`context_hash` ($C_0$)**: 推論時の解釈前提条件を条件固定再現性の境界として固定する暗号論的ハッシュ (SHA-256)。以下の8フィールドから構成される:
  1. `mb_version`: $M_B$ のバージョン識別子
  2. `mb_content_hash`: $M_B$ グラフ内容の暗号論的ハッシュ
  3. `target_domain`: 案件の対象ドメイン・業務境界 $B$
  4. `cascade_config`: 推論カスケード動作設定
  5. `llm_identity`: 外部推論器の固有アイデンティティ
  6. `cache`: Level 0 キャッシュの再現安定的なソート済みシリアライズ
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
  * グラフ更新（Leap や Rollback）によって `mb_version` が更新された場合、過去バージョンのキャッシュが現在境界へ誤適用される経路を閉じる。
  * 旧形式キャッシュの取り込みは、明示的な `migrate_legacy_cache(cache, source_mb_version)` 経由でのみ許可され、由来バージョン（provenance）なしでの自己昇格を禁止。
* **沈澱（Sedimentation）と観測保留（UNKNOWN ≠ FAILURE）の契約**:
  * 案件受付時（未確認時）にはライブ Level 0 キャッシュへ書き込まず、非同期ライフサイクルを経て `user_resolved == True` かつ `!human_rejected` が確認された段階で初めて `sediment_level0()` を呼び出す。
  * タイムアウトや未回収案件（UNKNOWN）は判断の誤り（FAILURE）ではなく未確定な未回収関係（$\xi$）の残存として扱い、ノード統計（`failure_count` / `confidence`）を悪化させず `unresolved_count` として独立記録。
* **意味的証拠鮮度と観測残差（`last_observed_at`）の直交分離**:
  * 監査用プロパティとして最新確定証拠時刻 `last_evidence_at = max(last_support_at, last_opposing_at)` を保持しつつ、関係拘束スコア（Core freshness）の計算には肯定的支持証拠時刻 `last_support_at` のみを用いる。
  * 反証証拠時刻 `last_opposing_at` は対向鮮度および歴史的反証拘束シグナル（破断検査側）へと直交伝播させる。
  * タイムアウト等の観測不能（UNKNOWN）は観測時刻 `last_observed_at` のみを更新し、`last_support_at` / `last_opposing_at` は保存される（「未確認放置案件による不当な鮮度リフレッシュ」の境界内遮断）。
  * グラフ同一性（`content_hash`）には行動力学・時間拘束に直結する `last_support_at` および `last_opposing_at` を包含し、過渡的観測残差 $\xi$ である `last_observed_at`、`unresolved_count`、`legacy_evidence_at` は除外する。
* **証拠極性の分離（Evidence Polarity Separation: 支持 vs 反証）**:
  * 証拠タイムスタンプを肯定的支持証拠（`last_support_at`）と否定的反証証拠（`last_opposing_at`）に分離。
  * 失敗・差し戻し（FAILURE / REJECTED）は `last_opposing_at` を更新し、`last_support_at` は保存されるため、失敗によって既存拘束 $C_{rel}$ の支持鮮度（`freshness`）が不当に上昇する逆転現象を根絶。
  * **厳格なフォールバック排除（Strict Polarity Isolation）**: `constraint.py` における支持鮮度（`freshness`）の計算は、すべて `last_support_at` のみから算出され、`last_updated` への暗黙フォールバック（対向のみノードが `last_opposing_at` 経由で支持鮮度を得る抜け穴）を完全に排除。`last_support_at` が未指定（`None`）のノードは即座に `freshness = 0.0` として計算される。
  * **フェイルクローズなレガシー移行 & セッター遮断**: 旧データ取り込み時、支持実績のみのノードは `last_support_at`、反証実績のみのノードは `last_opposing_at` へ移行するが、実績ゼロまたは支持・反証の双方が混在するノード（最後の更新極性が不明なノード）に対しては勝手に極性を捏造することを禁止（公理 B5）。タイムスタンプは `legacy_evidence_at`（$\xi$）としてのみ保持し、支持鮮度・対向鮮度はともに `0.0` とする。またレガシーセッター（`@last_evidence_at.setter`, `@last_updated.setter`）は `AttributeError` を送出する読み取り専用プロパティとし、極性曖昧な代入を型・契約レベルで遮断。
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
  * Canary 熱が閾値 $\theta_{canary}$ を超過した場合、またはシャドウ反実仮想評価で改悪率が許容限界を超えた場合、現在の Authority/Policy と閾値に従って本番バージョンからロールバックされる。

### 3.6 認知的ライフサイクルの分離（Description $\to$ Commitment $\to$ Active Constraint）
* **オブジェクト生成と支持証拠の厳格分離（BASE v2.0 §4.2: Description ≠ Commitment）**:
  * 単なる Python クラス `MBNode(...)` のインスタンス化（関係の記述・仮説定義）をもって、正の支持証拠 `last_support_at` や `freshness` を自己生成・捏造することを禁止。
  * **Constructor Forgery の境界内排除 (新P0)**: 公開コンストラクタ引数 `commitment_origin`, `committed_at`, `commitment_record` は契約上の遮断として無視・無効化され、バイパス引数（`_internal_commitment`）も API から撤去する。公開コンストラクタはいかなる引数を用いても未コミットノードしか生成できない。
  * **属性イミュータビリティ (P0-P1)**: コミットメント関連プロパティ（`commitment_origin`, `committed_at`, `commitment_record`）および内部保持フィールド `_commitment_record` への直接代入は `AttributeError` で拒絶される（契約上の遮断）。
* **正規コミットメントゲートウェイ（`MBGraph.commit_node()`）**:
  * 記述を $M_B$ の正統な構成要素として昇格・定着させる唯一の手段として `commit_node(node, origin, actor, authority_context, commit_time, evidence_time)` を規定。
  * コミットメントのバインドは、ゲートウェイ内部でのみ `object.__setattr__(node, "_commitment_record", rec)` を介して実行される。
  * `CommitmentOrigin`（`AUTHORITY`, `VERIFIED_EXPERIENCE`, `AUTHORITATIVE_SEED`, `PROMOTION`, `MIGRATION_VERIFIED`, `TEST_FIXTURE`）の明示指定を義務付け（デフォルト引数なし・未知の値は `ValueError` で即時拒絶）。
  * `origin=CommitmentOrigin.AUTHORITY` の場合は `AuthorityContext.is_authorized_for(domain)` が呼び出し可能かつ厳格に `True` を返すことを検証し、権限不足やドメイン管轄外の場合は `PermissionError` で即座にフェイルクローズ遮断。
  * **支持証拠時刻とコミット時刻の明確な分離**:
    * 証拠観測時刻 `last_support_at`（過去の検証・起案時刻 `evidence_time`）と、境界 $M_B$ への拘束定着時刻 `committed_at`（コミット時刻 `commit_time`）を分離記録。
    * 反証のみノード（`last_opposing_at` 保持かつ `last_support_at is None`）、レガシー曖昧ノード（`legacy_evidence_at` 保持）、および `MIGRATION_VERIFIED` 移行ノードに対しては、コミット時であっても支持証拠を捏造しない（極性隔離の徹底）。
  * **単一コミットメントモデル（再コミット・出所上書きの遮断）(P1)**:
    * `commit_node()` はすでにコミット済みのノード（`node.is_committed == True`）の再コミット試行を `ValueError` で即座に拒絶。一度確立されたコミットメント出所・刻印時刻・lineage の事後改ざん・上書きを防止する。
* **未コミットノードのフェイルクローズ境界内排除（多層防御）**:
  * `MBGraph.add_or_update(node)` は `node.is_committed` を厳格検証し、未コミットの記述オブジェクトの直接注入を `ValueError` で拒絶。
  * `InterpCascade`（推論カスケード）は未コミットノードを `eligible_nodes` および Level 0 キャッシュ参照から 100% 排除（未コミット記述のみでは即時 Tier 3 `ask_human` へ契約上フォールバック）。
  * `RelationConstraintLocator` は未コミットノードに対する主束縛解決を拒絶し、`locate_bundle_for_node()` は `None` を返却。
* **直列化データの自己申告偽造排除とロード時完全性照合 (P0-P1)**:
  * **`CommitmentRecord.from_dict_strict()`**: `from_dict()` におけるデフォルト値補完を全廃。直列化データ内の `origin`（既知Enum値検証）、`committed_at` / `evidence_at`（ISO-8601 時刻妥当性）、`actor`（必須）の厳格検証を行い、外側フィールドとの不一致や欠損は `ValueError` で拒絶。
  * **ロード時 `content_hash` 検証 (`IntegrityError`)**: `from_dict(verify_hash=True)` は、保存された `content_hash` と復元後グラフの実効 `content_hash()` を照合し、不一致時は `IntegrityError` を送出して改ざんデータを即座に遮断。
  * ※ `content_hash` はデータの「完全性・改ざん検出（Integrity/Checksum）」を境界内で検証するものであり、署名・認可による「真正性（Authenticity）」とは区別して運用される。
* **実データ検証を伴う真正な移行ゲートウェイ (`migrate_legacy_nodes()`) (P0-P2)**:
  * **互換ショートカットの全廃**: `snapshot: LegacySnapshot`, `context: MigrationContext` の型指定を厳格義務付け。文字列引数による暗黙呼び出しは `TypeError` で即時拒絶し、`admin` ロールへの自動昇格バックドアを根絶。
  * **スナップショット ↔ 対象ノードの同一性・内容完全束縛**:
    * グラフ内の未コミットノード集合とスナップショットの対象ノードID集合が境界内同値であること（`graph_uncommitted_ids == target_ids`）を照合。すり替え・余剰・不足がある場合は `IntegrityError` で遮断。
    * 各ノードの `domain`, `trigger_pattern`, `action_template` がスナップショットの raw payload と一致することを照合し、改ざん・不整合を `IntegrityError` で遮断。
  * 権限のないアクター（`role` が `admin`, `manager`, `migration_officer` 以外、または `capability != "legacy_migration"`）の移行試行を `PermissionError`、実データハッシュ不一致を `IntegrityError` で遮断し、実データハッシュを刻印した真正な `MIGRATION_VERIFIED` を確立。

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
| **Test 14** | 認知的ライフサイクル分離 | 純粋な `MBNode(...)` 記述生成では支持証拠を持たず、コンストラクタでのコミットメント自己捏造（Constructor Forgery / `_internal_commitment` バイパス）が遮断されること。コミットメント属性および `_commitment_record` の直接代入が `AttributeError` で拒絶されること。正規ゲートウェイ `commit_node()` を通過して初めて正統な出所・支持証拠打刻・不変 `CommitmentRecord` が付与され、再コミット試行が `ValueError` で遮断されること（単一コミットメントモデル）。`CommitmentRecord.from_dict_strict` による直列化データ自己申告偽造の排除、ロード時 `content_hash` 不一致時の `IntegrityError` 遮断、および `LegacySnapshot` + `MigrationContext` による互換ショートカット全廃（文字列引数 `TypeError`）・対象ノード境界内束縛（すり替え・内容不一致 `IntegrityError`）を伴う実検証移行が境界内で成立すること。 |

---

## 6. 結論と次期フェーズ展望

本文書で定義された `RDL_Enterprise v2.0` は、現時点の有限実装境界において、T0/BASE の主要契約（有限境界 $B$、未回収関係 $\xi$ の残存、代謝閉ループ、自己例外化禁止）を実装・検証した。
経験の蓄積に伴う計算コスト逓減（逆スケーリング）はシミュレーション上の有限条件下で検証され、Canary 熱隔離、権威方針分離、および観測保留（UNKNOWN ≠ FAILURE）規律によってエンタープライズ運用観測に進むための検証基盤を確立した。

次期フェーズ（Phase B）では、実業務データ連携、非同期分散キュー統合、およびマルチエージェント間の境界調停へと展開を進める。
