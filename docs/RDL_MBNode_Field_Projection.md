# MBNode Field Projection Map

`MBNode` はEnterpriseの運用構造であり、その全fieldをCoreへ移す対象ではない。Coreへ投影するfieldは、同じ有限境界で意味遷移を再構成するために必要なものだけとする。

| MBNode field | 初期分類 | Core投影方針 |
|---|---|---|
| `id` | Core identity | `NodeDescription.node_id` へ投影 |
| `domain` | Core identity | `NodeDescription.domain` へ投影 |
| `node_relations` | 関係候補 | 明示的に選択・検証したRelationだけを投影 |
| `source_id` / `source_lineage` | Provenance候補 | Provenanceへ写像し、欠損を捏造しない |
| `trigger_pattern` | transition-relevant候補 | 現在は保留。意味遷移への影響を定義してから投影 |
| `action_template` | intent / execution境界候補 | 実行方式をCoreへ持ち込まず、intent契約を先に定義 |
| `authority_level` | Policy / Authority | Core identityへ混入させず、AuthorityConstraintへ別投影 |
| `confidence` | 評価観測 | TruthやCommitmentへ変換しない。ConstraintStrength等へ明示写像 |
| `success_count` / `failure_count` / `approval_count` / `rejection_count` / `unresolved_count` | Enterprise統計 | 初期Core投影から除外。集計意味を定義してから別契約化 |
| `is_frozen` | Enterprise mutability control | CoreのBoundaryContextとは別の運用制御として扱う |
| `created_at` / evidence timestamps | 時間・Provenance | 意味遷移に影響する場合のみContext/Provenanceへ回収 |
| Commitment fields | Commitment境界 | Constructor投影せず、Core CommitmentRecordの正規経路を使う |

## Projection rule

初期Adapterは `id`、`domain`、明示Relation、Provenanceだけを投影する。`trigger_pattern` と `action_template` は、将来の遷移やActionへ影響する可能性があるため、現在のidentity projectionへ暗黙に含めない。

Coreへfieldを追加する場合は、次を満たすこと。

- fieldの意味遷移上の役割が定義されている
- 入力型と保持型が有限境界として分離されている
- ContextまたはProvenanceから回収できる
- Enterprise adapterでselected observation sliceをテストできる
- 追加fieldなしの場合との差分をbounded observationとして記録できる

