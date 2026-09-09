# RDL Coding Principles

## RDL実装規律

本書は、RDLの語彙をコードへ置換するための用語集ではない。RDLを実装した結果、有限境界・関係拘束・$ξ$・自己例外化禁止という認識論的条件が、暗黙の状態・型変換・テスト解釈によって破壊されないための実装規律である。

コード上の `exact`、`SUCCESS`、`oracle`、`confidence` などの識別子は、実装上必要であれば保持してよい。ただし、意味層では必ず有限境界 $B$、問い $Q$、時点 $t$、観測断面 $O$、運用目的 $Purpose$、権限・方針・閾値へ写像して読む。

以下で `MUST` は規範上の必須条件、`SHOULD` は正当な理由がある場合に限り逸脱できる推奨条件を示す。各節のEnterprise固有名は規範そのものではなく、参照実装上の例である。

## 1. 世界そのものをCoreの状態にしない

`world_truth`、`ground_truth`、`is_true`、`true_answer` をRDL Coreの内部状態として安易に導入しない。必要な外部参照は `environment_reference`、`fixture_condition`、`observed_outcome`、`external_reference` など、有限観測または実験条件として表現する。Simulationのoracleも真理ではなくfixtureである。

## 2. DescriptionとCommitmentを分離する

オブジェクト生成 MUST NOT imply Commitment or Active Constraint。Enterpriseの参照実装では `MBNode(...)` の生成は記述・候補の生成であり、$M_B$のActive Constraintではない。原則として、

```text
Description -> Candidate -> Evidence / Authority / Verification
             -> Commitment -> Active Constraint
```

を通過させる。constructor MUST NOT 自己の `support`、`freshness`、`authority`、`commitment` を生成する。

## 3. Evidence polarityを潰さない

`SUPPORT`、`OPPOSE`、`UNKNOWN`を別の状態として保持する。`UNKNOWN != FAILURE`、`OPPOSE != 弱いSUPPORT`、`timeout != rejection`、`absence of evidence != opposing evidence`である。`None -> False` のように不確定状態を失敗へ暗黙変換しない。

## 4. FとF'の解釈境界を固定する

`F`と`F'`は同じpre-update $M_B$で解釈し、その差分を$E = Δ(F,F')$として扱う。比較途中で$M_B$を更新してはならない。EnterpriseのSnapshot、ReplayToken、RunContextは、解釈に使った境界と時点を凍結・回収可能にする参照例である。

## 5. 外生条件を隠さない

意味遷移に影響する外生条件をCore深部から隠してはならない。Observation Time、Evidence Time、Commitment Time、Simulation Time、seed、外部モデル、検索結果のうち、F/F'、Constraint activation、Commitment、H、$M_Δ$、Actionに影響するもの MUST be recoverable through Context or Provenance。`datetime.utcnow()` や `random.random()` の直接呼出しは避ける。意味遷移に影響しないログ配送時刻、UI metadata、監査用wall clockなどは、意味境界の外部であることを明示すればよい。

## 6. AuthorityをTruthへ昇格させない

権威の存在は真理性を意味しない。作用可否は少なくとも `Authority x Scope x Target x Relation` として評価し、権限者であっても対象領域との関連性を別に検査する。Authorityは関係拘束であり、真理証明器ではない。

## 7. 強い状態を真理へ変換しない

高い `confidence`、大きな `support_count`、強いinertia、高いauthority、安定したreplay結果から `is_true = True` を導かない。これらは$M_B$内の拘束状態であり、$ξ$を消去しない。

## 8. 十分性を局所化する

`M_B is sufficient` ではなく、`operationally sufficient under B/Q/t/Purpose` として判定する。運用十分なら局所作用し、不十分ならLLM、Search、API、人間へ問い合わせて境界を拡張する。十分性は世界全体の十分性ではない。

## 9. LLM出力を直接Commitmentしない

LLM出力 MUST NOT be committed directly。LLM出力は候補関係材料であり、Evidence、Commitment、Truthではない。

```text
LLM -> Candidate -> RDL evaluation -> Adopt / Hold / Verify / HITL
```

を通過させる。LLMは未回収関係$ξ$の探索候補生成器であって、$ξ$の解消器ではない。

## 10. ReplayとVerificationを有限化する

同じhash、trace、seedは同じ世界を意味しない。指定したRunContextと観測境界での条件固定再現性、境界内同値、観測終了時状態の一致として読む。実装上の `exact=True` は保持できるが、意味層ではbounded equivalenceへ写像する。

## 11. State Digestを「全部」と呼ばない

State Digestは現在の観測境界で後続遷移に影響すると扱う遷移関連状態である。新しい状態変数が発見された場合は、完全状態が誤っていたと断定せず、$B_{state}$の拡張として扱う。

## 12. テスト成功を理論の真理性へ昇格させない

テスト成功が示すのは、指定された有限テスト集合と検査境界で契約違反が観測されなかったことまでである。Simulationはmechanism validationまたはstress evidenceであり、RDL理論の証明ではない。

## 13. RDL自身を例外にしない

「RDL helperだから」「Core内部だから」「system ruleだから」という理由で検査・来歴・更新管理を免除しない。threshold、promotion policy、authority rule、cache rule、LLM selection ruleも、通常の関係拘束と同じく境界・権限・provenance・検証対象である。

## 参照実装と規範の分離

RDL Coreへ移植する際は、Enterpriseのクラス名・API名をそのまま規範とみなさない。`MBNode`、`ReplayToken`、`RunContext`、`Authority` は、Description/Commitmentの分離、境界固定、権限拘束という規範を具体化した参照実装である。Gameでは `Rumor`、`Belief Candidate`、`Committed Belief`、`Behavioral Constraint` など別の型へ写像してよいが、規範上の関係は維持しなければならない。

## 実装レビュー時の最小チェック

- 世界の真理・現実・絶対安全を内部状態へ直接入れていないか
- Description、Candidate、Evidence、Commitment、Active Constraintを分離しているか
- `UNKNOWN`、`OPPOSE`、`timeout`を失敗へ潰していないか
- F/F'の解釈境界と外生条件をfreezeしているか
- Authority、Scope、Target、Relationを分離しているか
- 強い状態やテスト成功をTruthへ変換していないか
- LLM出力を検証前にCommitmentしていないか
- Replay、Digest、Verificationに有限境界があるか
- RDL自身の規則にもprovenanceと検証を適用しているか
