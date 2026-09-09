# RDL Core Extraction Gate

## 目的

本書は、RDL_EnterpriseからRDL Coreへ実装を抽出する際の受入条件を定める。抽出対象はファイル単位で自動的に決めず、BASE/SPEC、Operational Lexicon、RDL Coding Principles、Enterprise依存の順に確認する。

## 4段ゲート

```text
1. BASE / SPEC適合
        ↓
2. Operational Lexicon適合
        ↓
3. Normative Core Rules適合
        ↓
4. Enterprise依存の除去または境界化
        ↓
   Core candidate
```

各ゲートで不明な依存や未回収の外生条件がある場合、抽出を保留する。保留は失敗ではなく、境界またはProvenanceを追加するための作業状態である。

## 抽出受入条件

候補コードは、少なくとも次を満たさなければならない。

- Object creation MUST NOT imply Commitment or Active Constraint。
- `UNKNOWN` MUST NOT collapse into `FAILURE`、`OPPOSE`、または `SUPPORT`。
- Authority MUST NOT imply Truth。
- F/F' MUST use the same pre-update $M_B$。
- 意味遷移に影響する外生条件 MUST be recoverable through Context or Provenance。
- LLM output MUST NOT be committed directly。
- 強い拘束状態、Replay成立、テスト成功 MUST NOT be promoted to Truth or Completeness。
- RDL internal rules MUST NOT self-exempt from boundary、Provenance、Authority、Threshold、Verification。

## 初期候補の分類

### Core寄りの候補

関係拘束、Commitment、極性、有限境界、再演境界を直接扱う部分。候補として `mb_graph.py`、`constraint.py`、`h_state.py`、`snapshot.py` 内の境界・再演・観測モデル、`authority.py` の一般化可能な権限拘束を調査する。

### 境界層の候補

Coreの規範をEnterpriseまたはSimulationへ接続する部分。`runtime.py`、`cascade.py`、`human.py`、`simulation_adapter.py`、`social_adapter.py`、`rdl_simulation` の各モジュールは、Core依存と製品依存を分離してから判断する。

### Enterprise固有の候補

Canary、Shadow、Promotion、Durability、業務チケット、運用メトリクスに強く依存する部分。`canary.py`、`shadow.py`、`promotion_gate.py`、`durability.py` およびEnterprise固有のシナリオは、Coreへ直接移さずAdapterまたはPolicy境界として扱う。

## ContextとProvenanceの確認

外生条件は同じ入れ物に押し込めない。

- `Context`: 当該判断を再構成するための境界、時点、seed、設定、入力条件。
- `Provenance`: 条件・観測・拘束・権限がどこから来たか、誰がいつ提示したか、どの検証を経たか。

意味遷移に影響する条件は、Context and/or Provenanceから回収可能でなければならない。意味境界外の運用metadataは、境界外であることを明示する。

## 抽出後の確認

抽出後は、同じ有限RunContextでEnterprise側とCore側を比較し、世界の同一性ではなく、指定観測断面におけるbounded equivalenceを確認する。テスト成功は抽出契約の有限検証成立として記録し、理論の真理性や完全性へ昇格させない。
