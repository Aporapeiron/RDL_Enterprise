# RDL Enterprise 業務AI 動作特性パラメータ設計

*RDL Enterprise / Product Parameter Layer / DRAFT v0.1*  
*ステータス: 内部設計案 / 実務観測前 / 数値未確定*

## 0. 目的

既存実装に散在する閾値・重み・上限・伝播係数をそのまま利用者へ露出せず、意味ごとに束ねて少数の製品パラメータへ展開する。

```text
多数の内部パラメータ → 意味ごとに束ねる → 少数の利用者向け動作特性
```

初期候補は、違いへの感度、関係を見る広さ、人への確認、見直しの早さ、知識の定着の5軸とする。これはT0 Coreの基底措定ではなく、実務観測で改訂される製品仮説である。

## 1. T0との分離

パラメータで調整してよいのは、差異の扱い、参照範囲、確認頻度、再検査への移行、知識候補の再利用慎重度である。次は設定で変更してはならない。

```text
UNKNOWN ≠ UNRESOLVED ≠ NOT_EVALUATED
Observation ≠ Candidate ≠ Commitment ≠ Active
Authority ≠ Truth
credential verified ≠ human identity proven
persisted Observation ≠ current Observation
ActionLedger ≠ Truth
F と F' は同一の更新前 M_Bから形成する
有限Boundaryでも ξ は残る
```

## 2. 内部候補

### v0.1調査結果: 差異反応経路

現在の実装では、`CaseSnapshot.record_feedback()` がdispatch時に凍結した
同一 `M_B` / `FrozenInterpretationContext` から `F'` を再解釈する。
`F` と `F'` の差は、次の成分から `e_pred` として合算される。

```text
matched nodeの変化       0.5
action typeの変化         0.4
expected outcomeの変化    0.4
confidenceの差             0.4 * gap
回答contentの変化          0.2
```

入力境界の差は `e_input` として別に計算され、短い入力、category欠落、
新規知識提供などを含む。最終的に `e_pred` / `e_input` は
`EnterpriseRuntime._finalize_case_metabolism()` の `HState.add_heat()` と
`record_observation()` へ渡される。

この経路から、現時点では次を確認できる。

- `F` と `F'` は同一更新前Contextから形成される。
- prediction error と input error は別フィールドで保持される。
- `e_pred` はscalarだが、差異理由は説明文字列としても保存される。
- Runtimeには `difference_response_threshold` が追加されている。既定値は
  `None`（gate無効）で、既存の挙動を保持する。
- 明示値を指定した場合だけ `e_pred < threshold` を
  `BELOW_CURRENT_THRESHOLD` としてHへのprediction reactionを抑制する。
- raw `e_pred` と差異説明は保持され、threshold未評価・UNKNOWNはこのgateで
  成功やFailureへ再分類されない。

thresholdを実装する場合も、Observationや `e_pred` の記録を削除せず、
`observed_difference` と `reaction_status=below_current_threshold` を分離する。

### 違いへの感度 / DifferenceSensitivity

候補: `w_pred`, `w_input`, `gamma`, ξ_obs各重み、freshness sensitivity、opposing-signal thresholds、一部のconfidence/rupture閾値。`theta`は下流の維持・再編境界であり、単純に感度と同一視しない。

### 関係を見る広さ / RelationReferenceBreadth

現在の最小実装は `CascadeConfig.relation_breadth_limit` で、同一hopの
active relation candidate数だけを上限化する。ranking、relevance、伝播重み、
relation hop、M_Bそのものは変更しない。未指定 (`None`) なら従来の呼び出し側上限を使う。
relation traversal bound、`relevance_floor`、伝播重み、`constraint_boost_cap`、
複数hopはAdvancedまたは別問題であり、Basicへ混ぜない。

### 人への確認 / HumanConfirmationSensitivity

現在の最小実装は `EnterpriseRuntime.human_confirmation_threshold` で、confidence不足による
確認境界だけを上書きする。未指定なら既存の `min_confidence` を使う。`ask_human`、
`human_only`、`require_approval`、構造衝突、不可逆操作などのmandatory escalationは
この値で解除しない。`kappa_threshold`も別の警告経路として維持する。

### 見直しの早さ / RevisionAggressiveness

候補: `theta_0`、rupture thresholds、remaining heat ratio、candidate regression limits、canary heat/failure limits。`H >= theta`の状態区分自体は保持する。

### 知識の定着 / KnowledgeSedimentationCaution

候補: `min_survive_approvals`、`min_survive_relevance`、minimum cases/unique patterns、promotion/shadow/durability policyの一部。

```text
一度うまくいった ≠ Truth ≠ 自動的に M_Bへ定着
Observation → Candidate → Commitment → Active
```

## 3. Human側原型の翻訳

`Stabilize`は違いへの感度、`Explore`は探索・見直し、`Reinforce`は知識候補の強化へ限定して翻訳する。`Alert`は高影響業務に対する横断補正、`Affiliate`は関係・責任範囲の参考、`Buffer`と`Overdrive`は通常5軸外のAdvanced候補とする。権限の高さを真実性へ変換しない。

## 4. Basic / Advanced

```text
Basic preset → deterministic expansion → Advanced parameter set
```

Advanced値を個別変更した場合はBasic表示を「カスタム」とする。設定変更は、観測・候補・検証・Commitment・Activeのライフサイクルを経る。

## 5. 実務観測と未確定事項

案件完了率、人間介入、作業時間、手戻り、見逃し、誤検知、再編、回帰、コスト、レイテンシを観測する。5軸の最終性、具体的変換式、preset数値、業務別推奨値、relation depthの有効範囲、相互作用、自動調整の安全範囲、実務コスト優位性は未確定である。

## 6. 推奨段階

1. 人間が手動調整する。
2. RDLが変更候補を提案する。
3. 明示された低リスク範囲だけ限定自動調整する。

本Draftでは値を決めず、`docs/PARAMETER_INVENTORY.md` の現状台帳と実務観測を先に置く。新しいglobal `RuntimeParameterConfig`や5軸Profileは、consumerと効果が実証されるまで導入しない。
