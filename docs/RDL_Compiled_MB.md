# RDL Compiled M_B

## 位置づけ

本書は、RDLにおける`Function = Compiled M_B`の意味境界を定める。ここでいうCompiled M_Bは、有限境界内で採用された関係構造を、一定のFunction identity/versionを持つ実行形式へ固定したものである。

```text
FunctionDescription
= Compiled M_B の identity

FunctionInvocation
= FunctionDescription
  + BoundaryContext
  + Purpose
  + config
  + Provenance
```

Compiled M_BはTruth、Reality、完全状態、または終端的なCommitmentではない。Functionの存在・version・実行結果はいずれも、指定された`B / Q / t / O / Purpose`に対する有限な性質として扱う。

## 更新経路

```text
Working M_B
    ↓ experience / observation
Adaptive M_B
    ↓ similarity / structure inspection
Function Candidate
    ↓ validation / rupture / durability
Compiled M_B vNext
```

Compiled M_Bは通常運用中は固定されるが、固定性は絶対不変を意味しない。検査、反例、Boundary変更、Authority/Policy変更、version更新によって再コンパイル対象になる。

## 規範

- `FunctionDescription` MUST identify a Function with a non-empty `function_id` and `version`.
- Function identity/version MUST NOT be interpreted as Truth, Authority, Commitment, or Active Constraint.
- Function application MUST be recoverable through `FunctionInvocation` or an equivalent Observation record containing Boundary and Provenance information.
- Function output MUST remain an Observation or bounded evaluation result until an explicit Commitment経路を通る。
- Compiled M_B MUST NOT erase the conditions, evaluator identity, or Provenance needed to interpret its output.
- Version変更 MUST be treated as a new bounded Function identity;旧Functionとの一致を暗黙に仮定しない。
- 再コンパイル候補 MUST NOT be committed directly; validation and用途別Promotion Policyを通す。

## 時間スケール

時間スケールは現時点ではCoreの固定enumではなく、運用分類として扱う。

```text
Working M_B   : 秒〜時間。現在の会話・案件・局所状態
Adaptive M_B  : 時間〜週。経験・局所関係・競合候補
Persistent M_B: 長期。安定した業務構造・方針
Compiled M_B  : 通常運用では固定。検査とversion更新で変更
```

## 循環

日常運用は、Compiled M_Bを用いてObservationを形成し、Working M_Bと有限判断へ接続する。

```text
Input → Compiled M_B / Function → Working M_B → F → Action → E
```

学習・再編は、経験からAdaptive M_Bを整理し、候補を検査してCompiled M_Bへ戻す。

```text
Experience → Adaptive M_B → Structure Inspection
→ Function Candidate → Validation → Compiled M_B vNext
```

どちらの循環でも、Functionの成立は`ξ`を消去しない。
