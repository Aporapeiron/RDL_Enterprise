# RDL Trigger Semantics

## 目的

Enterprise `MBNode.trigger_pattern` は、現在の実装ではCascadeのmatching入力である。これはそのままRDL CoreのConstraintやTruthへ移植できる意味型ではない。本書は、Coreへ昇格できる意味と、Enterprise/Adapterに留める意味を分離する。

## 現行fieldの分類

| field | 現行の役割 | 初期境界 |
|---|---|---|
| `exact_keys` | queryとのLevel 0 matching候補 | 観測・記述候補。Coreへ入れる場合もRelation/Constraintとは別契約 |
| `rule_expr` | 正規表現等によるLevel 1 matching policy | Enterprise matching policy。式の実行器をCoreへ持ち込まない |
| `embedding` | Level 2の意味類似候補 | 外部モデル・表現空間依存。モデルidentity/version/ProvenanceなしにCoreへCommitmentしない |

## 規範

- `trigger_pattern` の生成 MUST NOT imply Commitment or Active Constraint。
- matching成立 MUST NOT imply Truth。成立するのは指定された問い・Boundary・matching policyにおける観測結果である。
- `exact_keys`、`rule_expr`、`embedding` の結果を同じpolarityへ暗黙変換しない。
- `rule_expr` の評価結果は、式・engine identity/version・入力Boundaryを回収可能にする。
- `embedding` の評価結果は、モデル・埋め込みversion・距離基準・入力Boundaryを回収可能にする。
- matching不能、timeout、候補不在を `OPPOSE` や `FAILURE` へ暗黙変換しない。

## Core昇格条件

Trigger semanticsをCoreへ追加する場合は、少なくとも次を満たす。

```text
TriggerDescription
  ↓ explicit matching contract
MatchingObservation
  ↓ BoundaryContext + evaluator identity/version + Provenance
Relation / Constraint observation
```

単に`trigger_pattern`辞書を`NodeDescription.attributes`へコピーすることは、Core抽出とはみなさない。

