# RDL Enterprise Product Status v0.1

この文書は、RDL Enterpriseを業務AIとして運用するための現在の有限Boundaryを記録します。完了判定は、すべての環境・業務・外部サービスに対する完全性を意味しません。

## Current Boundary

現在のv0業務AIランタイムは、単一RuntimeとSQLite永続化を前提にします。

```text
authenticated actor
  -> EnterpriseService
  -> RDL Runtime interpretation
  -> workflow connector / tool
  -> ActionLedger
  -> feedback
  -> atomic persistence and metabolic learning
```

## Completed Product Layers

- **Persistence / Restart Recovery P1**: pending/resolved case、M_B、HState、Level0 cache、経験履歴、再編proposal、canary deployment、ActionLedgerを再起動後に復元します。
- **AuthN / AuthZ P2a**: authenticated `AuthorityContext`、domain scope、feedbackのticket identity、actor provenanceを検査・記録します。
- **Tool Execution P2b**: effectful toolのoperation identity、payload binding、durable `PLANNED`、成功・失敗・未実行・不確定、provider reconciliation、retry safetyを扱います。
- **Workflow vertical slice**: `WorkflowConnector`によるread-only lookupを、認証済み業務入力とToolRegistryへ接続しています。

## Explicit Limits

- `WorkflowConnector`は現在、有限fixtureまたは同等のread-only adapterです。外部workflow製品への本番接続ではありません。
- tool selectionとpayload構成は、現時点では呼び出し側が指定します。RDLの解釈結果からtool candidateを自動選択する経路は未実装です。
- `IRREVERSIBLE` toolのdurable ApprovalRecordは未実装で、現在の製品Boundary外です。v0ではDRY_RUN_ONLY、REVERSIBLE、COMPENSATABLEを対象にします。
- AuthenticationContextは上流で認証済みという前提の内部service境界です。IdP token/API keyの実検証は外部接続時に追加します。
- SQLiteは現時点で単一Runtimeを前提にします。multi-worker競合、外部API境界、payload schema migrationは未閉鎖です。

## Acceptance Direction

次の製品作業は新しいCore型を増やすことではなく、実業務connectorを一つずつ接続し、

```text
business input
  -> interpretation
  -> authorized tool candidate
  -> execution
  -> operator-visible result
  -> feedback
  -> persistence / learning
```

を縦に検証することです。失敗や情報不足が実際に現れた境界だけを次の設計対象にします。
