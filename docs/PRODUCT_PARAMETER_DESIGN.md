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

### 違いへの感度 / DifferenceSensitivity

候補: `w_pred`, `w_input`, `gamma`, ξ_obs各重み、freshness sensitivity、opposing-signal thresholds、一部のconfidence/rupture閾値。`theta`は下流の維持・再編境界であり、単純に感度と同一視しない。

### 関係を見る広さ / RelationReferenceDepth

候補: relation traversal bound、active subgraph limit、`relevance_floor`、伝播重み、`constraint_boost_cap`。現在の1-hopはT0不変条件ではなく、現行実装の有限境界である。

### 人への確認 / HumanConfirmationSensitivity

候補: `min_confidence`、`kappa_threshold`、HITL条件、escalation policy。人間承認が安全上必須の操作をこの軸で解除してはならない。

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
