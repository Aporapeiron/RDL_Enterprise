# RDL Enterprise - 関係力学に基づく業務AI Runtime / Reference Implementation

RDL Enterpriseは、RDLの意味境界を業務AI Runtimeとして検証する参照実装です。LLMをAIの本体とせず、判断、観測、権限、外界作用、証跡を分離して扱います。

このリポジトリはRDLの完全実装や、世界の真理を決定するシステムを宣言しません。すべての一致、再現、十分性、安全性は、明示された有限Boundaryにおける性質です。

## Concept

RDL Enterpriseが目指すのは、常に正しい知識を持つAIではありません。

実務では、情報は欠け、規則は古くなり、担当や権限は変わり、昨日まで有効だった判断も破れます。RDLは、そのような変化や破断を例外として排除するのではなく、**有限な観測の中で判断し、壊れた箇所を見つけ、必要な範囲だけ更新しながら仕事を続ける**ための構造を扱います。

```text
観測する
  -> 現在使える構造で判断する
  -> 破断・不足・矛盾が現れる
  -> 必要な範囲だけ人間や外部系へ戻る
  -> 検証された変更だけを定着させる
  -> 再び仕事を続ける
```

したがって、RDLでは「一度成功した」「LLMがそう答えた」「権限者がそう言った」という事実を、そのままTruthへ昇格させません。Observation、Candidate、Commitment、Active、Authority、履歴を分離し、現在の有限Boundaryで何が使えるかを管理します。

目標は完全性ではなく、**変化や破断を含む業務の中で、判断・再判断・手戻りのコストを下げながら運用を継続できること**です。

## 現在動いているもの

現在の製品入口は、自然文からのread-only Jira/JSM照会です。

```text
自然文 -> routing -> Tool Candidate -> Bearer / Authority check
      -> Jira REST observation -> bounded result -> ActionLedger
      -> SQLite persistence -> process restart recovery
```

例えば、`IT-3って今どうなってる？`を受けると、対象issueを推測で補わず、`atlassian.jira.issue.lookup`のread-only Candidateを生成してから既存のAuthorityとTool boundaryを通してJiraを観測します。曖昧な`VPNの件どうなった？`は`UNRESOLVED`のまま実行しません。

実証済みの製品経路は、自然文routing、Authority/domain scope検査、Jira応答の`case_id`・`summary`・`status`・`owner`へのbounded projection、`assignee=null`の`owner=None`保持、ActionLedger、SQLite永続化、別Pythonプロセスでの再起動復元です。実環境のJira issue `IT-3` / `IT-4`もread-only lookup確認済みです。

## Operational Boundary

```text
localhost (127.0.0.1)
  + static Bearer service entry
  + single service principal
  + single Runtime / single-writer SQLite
  + read-only Jira provider
  + durable ActionLedger
```

外部credentialは環境変数から読み込み、内部結果、Ledger、SQLiteへ保存しません。Jiraの外部応答はそのまま内部状態へ流さず、必要な4項目へ投影します。

## 守っている意味境界

```text
credential verified       != human identity proven
Observation               != Tool Candidate != Commitment != Active
persisted Observation     != current Observation
ActionLedger              != Truth
Authority                 != Truth
UNKNOWN                   != UNRESOLVED != NOT_EVALUATED
Similarity                != Rupture
```

Candidate生成は実行を意味しません。read-only queryは明示的なreplay指定がない限り毎回providerを再観測します。provider observationやLedgerは有限な証跡であり、無条件の真実として扱いません。

## 知識沈澱とコスト軽量化

Tier 3で解けたことだけで、知識が自動的に`M_B`へ沈澱するわけではありません。

```text
検証された知識候補 -> 所定の更新経路 -> M_B / cacheへ定着
                                      -> 再利用可能なら後続コスト低下
```

反復構造、検証・教育、再利用回数によって低コスト経路へ移行し得ます。未学習パターンや人間確認が必要な案件が常に低コスト化することは主張しません。

## Benchmark

`benchmark_cost_curve.py`による合成ワークロードの観測値です。実API課金額ではなく、Tierごとのtoken-equivalentモデルです。

```text
Synthetic workload: 60 tickets
Pure LLM        108,000 token-equivalent
Standard RAG     60,000 token-equivalent
RDL Enterprise   46,800 token-equivalent
vs Pure LLM      -56.7%
vs Standard RAG  -22.0%
```

この削減率はtraffic mix、seed coverage、反復率、人間による知識供給に依存します。未学習のhardware系パターンはhuman overrideなしではTier 3に残留しました。これは再現条件付きの観測値であり、一般的な性能保証ではありません。

## Live Acceptance

```text
Windows + separate Python process + Bearer authentication
  + real Atlassian Jira + real IT-3 / IT-4 lookup
  + owner=null preservation + SQLite + process restart
  + credential string not detected in tested DB
```

これは現在のcredential、database、provider、localhost構成に対する有限な受入です。完全なsecret非漏洩や外部環境全般の安全性を証明するものではありません。

## これは確立していないこと

- internet-facing security、人間のidentityそのものの証明
- multi-process / distributed durability、tamper-proof audit
- universal secret non-leakage、Jira以外のprovider互換性
- RDLの完全性や普遍的な真理性
- 不可逆Toolのdurable Approval

## Quick Start

### Read-only Jira API

credentialをファイルへ書かず環境変数から渡します。

```powershell
$env:RDL_API_BEARER_TOKEN = "<local-api-token>"
$env:RDL_API_STORE_PATH = "D:\GitHub\RDL_Enterprise\data\rdl_api.sqlite3"
$env:RDL_ATLASSIAN_BASE_URL = "https://<your-domain>.atlassian.net"
$env:RDL_ATLASSIAN_EMAIL = "<service-email>"
$env:RDL_ATLASSIAN_TOKEN = "<api-token>"
python .\rdl_api.py
```

```powershell
$body = @{ text = "IT-3って今どうなってる？" } | ConvertTo-Json -Compress
$bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
Invoke-WebRequest -UseBasicParsing -Method Post `
  -Uri "http://127.0.0.1:8765/query" `
  -Headers @{ Authorization = "Bearer <local-api-token>" } `
  -ContentType "application/json; charset=utf-8" -Body $bytes
```

### CLI

```powershell
python .\rdl_query.py "IT-3って今どうなってる？" --actor-id local-operator
python .\rdl_query.py "IT-3って今どうなってる？" --actor-id local-operator --json
```

### Simulation and tests

```powershell
python .\run_simulation.py
python .\run_multiagent_sim.py --scenario lifecycle --days 60
python .\benchmark_cost_curve.py
python -m pytest -q
```

### Manual sedimentation benchmark

合成マニュアルと合成案件だけを使い、教育深度、再利用、例外停止、修復後回帰を比較する構造特性ベンチマークです。実務価値や普遍的な正しさを証明するものではありません。

```powershell
python .\benchmark_manual_sedimentation.py
```

結果は`benchmark_results/manual_sedimentation_latest.json`と`.csv`へ出力されます。D1/D2/D3は同一manual・同一case setの保持深度だけを変え、golden outcomeはRuntimeへ渡しません。

## 現在の主要構成

```text
rdl_api.py                         localhost API起動入口
rdl_query.py                       read-only query CLI
src/rdl_enterprise/http_api.py     HTTP、Bearer、localhost boundary
src/rdl_enterprise/business_query.py 自然文query orchestration
src/rdl_enterprise/tool_routing.py Tool Candidate生成
src/rdl_enterprise/tool_execution.py ToolRegistryと実行状態
src/rdl_enterprise/atlassian_jira_provider.py Jira read-only adapter
src/rdl_enterprise/service.py       認証済みservice境界
src/rdl_enterprise/persistence.py   SQLite persistence
src/rdl_enterprise/runtime.py       業務Runtimeとrestart recovery
src/rdl_enterprise/presentation.py  人間向け結果表示
tests/test_product_acceptance.py    製品受入・永続化・縦断テスト
docs/RDL_Product_Status_v0.1.md     製品Boundaryと残件
```

詳細なRDL原則、Coreの役割分離、参照先、Compiled M_Bの契約は`docs/`以下を参照してください。

## 次の評価境界

次は基盤を無制限に拡張することではなく、複数の実Jira案件をread-onlyで観測します。

```text
実務観測 -> 複数Jira案件 -> 人間が「今日見る案件」を選ぶ
         -> RDL候補との差を記録 -> 時間、見落とし、手戻り、HITLコストを測る
```

実際の破断や情報不足が現れた境界だけを、次の設計対象にします。
