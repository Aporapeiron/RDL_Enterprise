# Parameter Inventory v0.1

## 1. Scope

This is a static inventory of numeric defaults, thresholds, weights, limits,
durations, hop counts, and policy switches used by the current Enterprise
runtime. It documents the current behavior; it does not tune, replace, or
introduce a runtime parameter profile.

The inventory covers the primary runtime path and the adjacent durability,
promotion, shadow, canary, provider, simulation, and persistence paths. A
value being listed does not mean that it is safe or useful to expose to an
operator.

## 2. Baseline

- Baseline reference: `d284f6a8f1ddeeadb53fc4b3ed52b5a76b34764b`
- Current behavior was checked with `python -m pytest -q`.
- No production source behavior was changed for this inventory.
- The manual sedimentation benchmark remains a separate synthetic benchmark;
  its relation trace records stored, consumed, and effective relations separately.

## 3. Classification

The table uses these definition classes:

- `NAMED_CONFIG`: named field in a configuration or policy object.
- `NAMED_DEFAULT`: named constructor or method default.
- `LOCAL_CONSTANT`: a local value with an explicit semantic role.
- `EMBEDDED_MAGIC`: a numeric value embedded in an expression or branch.
- `STRUCTURAL_FIXED`: a bounded structural rule, not an operator setting.
- `DERIVED`: computed from other values.

The state column records the strongest conclusion available from static
inspection and the existing tests:

- `CONSUMED`: a current execution path reads the value.
- `EFFECTIVE`: existing code/tests show that changing it can affect behavior.
- `INACTIVE`: defined but not read by the inspected primary path.
- `UNRESOLVED`: static inspection does not prove behavioral effect.

## 4. Parameter Inventory

| parameter | current value | location | consumer / runtime path | semantic role | definition | state | tunability / axis | T0-sensitive notes |
|---|---:|---|---|---|---|---|---|---|
| `theta_0` | 2.0 | `runtime.py:EnterpriseRuntime` | `HState.theta_eff`, leap/reorganization gate | base heat threshold | NAMED_DEFAULT | EFFECTIVE | ADVANCED_TUNABLE / update aggressiveness | Do not confuse with rupture truth |
| `gamma` | 0.05 | `runtime.py`, `h_state.py` | `HState.dissipate` | heat cooling | NAMED_DEFAULT | EFFECTIVE | ADVANCED_TUNABLE / error sensitivity | Does not erase unresolved evidence |
| `w_pred` | 1.0 | `h_state.py:HState` | `HeatVector.total`, version/global heat | prediction-error weight | NAMED_DEFAULT | CONSUMED | ADVANCED_TUNABLE / error sensitivity | weighting is not truth |
| `w_input` | 0.4 | `h_state.py:HState` | `HeatVector.total` | input-error weight | NAMED_DEFAULT | EFFECTIVE | ADVANCED_TUNABLE / error sensitivity | keep distinct from prediction error |
| `remaining_ratio` | 0.2 | `h_state.py` | `apply_remaining_heat_after_leap` | heat retained after leap | NAMED_DEFAULT | CONSUMED | ADVANCED_TUNABLE / update aggressiveness | derived transition policy |
| `level2_threshold` | 0.35 | `cascade.py:CascadeConfig` | `InterpCascade.interpret_efp` | entry into Level 2 | NAMED_CONFIG | EFFECTIVE | BASIC_DERIVED / reference depth | not a semantic status gate |
| `cost_tier0_confidence_boost` | 0.1 | `cascade.py:CascadeConfig` | Level 0 prediction | cache confidence adjustment | NAMED_CONFIG | CONSUMED | SYSTEM_INTERNAL / cost | cache hit is not current truth |
| `llm_default_confidence` | 0.5 | `cascade.py:CascadeConfig` | Level 3 fallback | default external inference confidence | NAMED_CONFIG | CONSUMED | SYSTEM_INTERNAL / HITL | must not auto-commit |
| `level2_max_confidence` | 0.85 | `cascade.py:CascadeConfig` | Level 2 prediction | confidence ceiling | NAMED_CONFIG | EFFECTIVE | ADVANCED_TUNABLE / error sensitivity | confidence is not authority |
| `active_subgraph_limit` | 4 | `cascade.py:select_active_constraint_subgraph` | eligible-node selection | maximum active nodes | NAMED_DEFAULT | CONSUMED | BASIC_DERIVED / relation depth | finite boundary |
| `relevance_floor` | 0.05 | `cascade.py:select_active_constraint_subgraph` | seed-node selection | minimum query relevance | NAMED_DEFAULT | EFFECTIVE | BASIC_DERIVED / relation depth | no match is not failure |
| `relation_hop` | 1 | `cascade.py` | explicit relation propagation | traversal depth | STRUCTURAL_FIXED | CONSUMED | INVARIANT / relation depth | structural bound, not free tuning |
| `propagation_support_weight` | 0.8 | `cascade.py` | support/authority edge propagation | edge score | LOCAL_CONSTANT | CONSUMED | SYSTEM_INTERNAL / relation depth | relation strength is contextual |
| `propagation_unknown_weight` | 0.4 | `cascade.py` | unknown edge propagation | uncertain edge score | LOCAL_CONSTANT | CONSUMED | SYSTEM_INTERNAL / relation depth | unknown must remain unresolved |
| `cooccurrence_weight` | 0.5 | `cascade.py` | co-occurring-node expansion | inferred adjacency score | LOCAL_CONSTANT | UNRESOLVED | SYSTEM_INTERNAL / relation depth | inferred is not explicit relation |
| `authority_edge_weight` | 0.1 | `cascade.py` | candidate ranking | authority contribution | LOCAL_CONSTANT | CONSUMED | SYSTEM_INTERNAL / relation/context | authority is not truth |
| `constraint_boost_cap` | 0.15 | `constraint.py:ConstraintConfig` | `InterpCascade._constraint_boost` | capped confidence contribution | NAMED_CONFIG | EFFECTIVE | ADVANCED_TUNABLE / relation depth | survive only; break/unresolved boost zero |
| `w_relevance` | 0.35 | `constraint.py:ConstraintConfig` | locator score | relevance weight | NAMED_CONFIG | EFFECTIVE | BASIC_DERIVED / relation depth | context-bound |
| `w_freshness` | 0.25 | `constraint.py:ConstraintConfig` | locator score | freshness weight | NAMED_CONFIG | EFFECTIVE | BASIC_DERIVED / error sensitivity | stale is not false |
| `w_authority` | 0.15 | `constraint.py:ConstraintConfig` | locator score | authority weight | NAMED_CONFIG | CONSUMED | BASIC_DERIVED / human sensitivity | authority is not truth |
| `w_source` | 0.15 | `constraint.py:ConstraintConfig` | locator score | source support weight | NAMED_CONFIG | CONSUMED | ADVANCED_TUNABLE / evidence |
| `w_convergence` | 0.10 | `constraint.py:ConstraintConfig` | locator score | independent convergence | NAMED_CONFIG | CONSUMED | ADVANCED_TUNABLE / evidence |
| `freshness_half_life_days` | 90.0 | `constraint.py:ConstraintConfig` | freshness calculation | temporal decay | NAMED_CONFIG | EFFECTIVE | ADVANCED_TUNABLE / error sensitivity | time boundary must be retained |
| `bridge_coverage_drop_threshold` | 0.4 | `constraint.py:ConstraintConfig` | bridge detection | coverage loss threshold | NAMED_CONFIG | CONSUMED | ADVANCED_TUNABLE / relation depth |
| `rupture_freshness_threshold` | 0.2 | `constraint.py:ConstraintConfig` | rupture probe | stale-evidence signal | NAMED_CONFIG | CONSUMED | ADVANCED_TUNABLE / update aggressiveness | rupture is not revision |
| `rupture_rejection_ratio_threshold` | 0.4 | `constraint.py:ConstraintConfig` | rupture probe | rejection ratio signal | NAMED_CONFIG | CONSUMED | ADVANCED_TUNABLE / error sensitivity |
| `rupture_opposing_freshness_threshold` | 0.7 | `constraint.py:ConstraintConfig` | rupture probe | fresh opposing evidence | NAMED_CONFIG | CONSUMED | ADVANCED_TUNABLE / error sensitivity |
| `rupture_opposing_signal_threshold` | 1.2 | `constraint.py:ConstraintConfig` | rupture probe | accumulated opposition | NAMED_CONFIG | CONSUMED | ADVANCED_TUNABLE / update aggressiveness |
| `rupture_opposing_support_freshness_cap` | 0.5 | `constraint.py:ConstraintConfig` | rupture probe | cap on stale support | NAMED_CONFIG | CONSUMED | ADVANCED_TUNABLE / evidence |
| `min_survive_approvals` | 3 | `constraint.py:ConstraintConfig` | survival evaluation | minimum approvals | NAMED_CONFIG | EFFECTIVE | BASIC_DERIVED / sedimentation caution |
| `min_survive_relevance` | 0.4 | `constraint.py:ConstraintConfig` | survival evaluation | minimum relevance | NAMED_CONFIG | CONSUMED | BASIC_DERIVED / sedimentation caution |
| `kappa_threshold` | 0.2 | `human.py:HumanQuery` | HITL evaluation | inertia warning | NAMED_DEFAULT | EFFECTIVE | BASIC_DERIVED / human sensitivity | warning is not rejection |
| `min_confidence` | 0.4 | `human.py:HumanQuery` | HITL evaluation | confidence gate | NAMED_DEFAULT | EFFECTIVE | BASIC_DERIVED / human sensitivity | low confidence is not false |
| `minimum_cases` | 1 | `promotion_gate.py:PromotionPolicy` | promotion verification | minimum resolved cases | NAMED_CONFIG | CONSUMED | BASIC_DERIVED / sedimentation caution |
| `minimum_unique_patterns` | 1 | `promotion_gate.py:PromotionPolicy` | promotion verification | diversity minimum | NAMED_CONFIG | CONSUMED | BASIC_DERIVED / sedimentation caution |
| `max_allowed_regression_rate` | 0.05 | `promotion_gate.py:PromotionPolicy` | promotion/shadow gate | tolerated regression | NAMED_CONFIG | EFFECTIVE | ADVANCED_TUNABLE / update aggressiveness |
| `require_human_approval` | true | `promotion_gate.py:PromotionPolicy` | promotion authorization | approval requirement | NAMED_CONFIG | CONSUMED | INVARIANT for protected actions | candidate is not commitment |
| `require_durability` | true | `promotion_gate.py:PromotionPolicy` | promotion gate | durability requirement | NAMED_CONFIG | CONSUMED | INVARIANT / deployment |
| `require_shadow` | true | `promotion_gate.py:PromotionPolicy` | promotion gate | shadow requirement | NAMED_CONFIG | CONSUMED | INVARIANT / deployment |
| `minimum_resolved_cases` | 1 | `shadow.py:ShadowEvaluator` | shadow decision | minimum triplets | NAMED_DEFAULT | CONSUMED | BASIC_DERIVED / sedimentation caution |
| `initial_canary_ratio` | 0.1 | `canary.py:CanaryManager` | canary deployment | initial traffic share | NAMED_DEFAULT | EFFECTIVE | ADVANCED_TUNABLE / deployment |
| `theta_canary` | 1.5 | `canary.py:CanaryDeployment` | canary rollback | heat threshold | NAMED_CONFIG | EFFECTIVE | ADVANCED_TUNABLE / update aggressiveness |
| `max_allowed_failures` | 1 | `canary.py:CanaryDeployment` | canary rollback | failure count threshold | NAMED_CONFIG | CONSUMED | ADVANCED_TUNABLE / deployment |
| `max_allowed_failure_rate` | 0.05 | `canary.py:CanaryPolicy` | canary completion | tolerated failures | NAMED_CONFIG | EFFECTIVE | ADVANCED_TUNABLE / deployment |
| `max_allowed_heat` | 1.0 | `canary.py:CanaryPolicy` | canary completion | tolerated heat | NAMED_CONFIG | CONSUMED | ADVANCED_TUNABLE / deployment |
| `minimum_successes` | 1 | `canary.py:CanaryPolicy` | canary completion | minimum successes | NAMED_CONFIG | CONSUMED | BASIC_DERIVED / deployment |
| `provider_timeout` | 5.0 | provider adapters | HTTP request | transport timeout | NAMED_DEFAULT | CONSUMED | SYSTEM_INTERNAL / durability |
| `compensation_timeout` | 3.0 | `canary.py` | external compensation | transport timeout | NAMED_DEFAULT | CONSUMED | SYSTEM_INTERNAL / durability |
| `max_allowed_regression_rate` | 0.05 | `shadow.py` | shadow gate | candidate regression limit | NAMED_DEFAULT | CONSUMED | ADVANCED_TUNABLE / update aggressiveness |
| `timeout_interval_ticks` | 16 | `simulation_adapter.py` | simulation timeout scan | simulation scheduling | NAMED_DEFAULT | INACTIVE for production API | SYSTEM_INTERNAL |
| `case_id_path_escape` | safe="" | provider adapters | URL construction | path boundary | STRUCTURAL_FIXED | CONSUMED | INVARIANT / security | identifier must not alter path |
| `xi_obs.unclassified_weight` | 0.3 | `h_state.py:theta_eff` | ξ observation score | unclassified-input share | EMBEDDED_MAGIC | CONSUMED | ADVANCED_TUNABLE / error sensitivity | status categories remain distinct |
| `xi_obs.missing_weight` | 0.2 | `h_state.py:theta_eff` | ξ observation score | missing-information share | EMBEDDED_MAGIC | CONSUMED | ADVANCED_TUNABLE / error sensitivity | missing is not false |
| `xi_obs.unknown_weight` | 0.3 | `h_state.py:theta_eff` | ξ observation score | unknown-input share | EMBEDDED_MAGIC | CONSUMED | ADVANCED_TUNABLE / error sensitivity | unknown is not failure |
| `xi_obs.rejection_weight` | 0.2 | `h_state.py:theta_eff` | ξ observation score | rejection share | EMBEDDED_MAGIC | CONSUMED | ADVANCED_TUNABLE / error sensitivity |
| `theta_eff_reduction_cap` | 0.8 | `h_state.py:theta_eff` | effective heat threshold | maximum ξ reduction | EMBEDDED_MAGIC | CONSUMED | ADVANCED_TUNABLE / update aggressiveness | derived guard, not semantic status |
| `theta_eff_floor` | 0.5 | `h_state.py:theta_eff` | effective heat threshold | minimum threshold | EMBEDDED_MAGIC | CONSUMED | SYSTEM_INTERNAL / safety | prevents threshold collapse |
| `cooling_rate_cap` | 0.3 | `h_state.py:dissipate` | heat cooling | maximum local cooling rate | EMBEDDED_MAGIC | CONSUMED | SYSTEM_INTERNAL / error dynamics |
| `default_node_inertia` | 0.5 | `h_state.py:dissipate` | heat cooling | fallback inertia | EMBEDDED_MAGIC | CONSUMED | SYSTEM_INTERNAL / error dynamics | absent inertia is not zero inertia |
| `canary_heat_inheritance_ratio` | 0.5 | `h_state.py:inherit_canary_state_to_prod` | canary completion | heat transferred to production | NAMED_DEFAULT | CONSUMED | ADVANCED_TUNABLE / deployment |
| `canary_case_input_weight` | 0.4 | `canary.py:record_case` | canary heat | input error contribution | EMBEDDED_MAGIC | CONSUMED | ADVANCED_TUNABLE / error sensitivity |
| `canary_failure_confidence_cutoff` | 0.5 | `canary.py:record_case` | canary failure classification | prediction error cutoff | EMBEDDED_MAGIC | CONSUMED | ADVANCED_TUNABLE / update aggressiveness |
| `relevance_match_score` | 0.8 | `constraint.py:_compute_relevance` | relevance calculation | exact-key match score | LOCAL_CONSTANT | CONSUMED | SYSTEM_INTERNAL / relation depth |
| `authority_policy_score` | 0.9 | `constraint.py:_compute_authority_weight` | authority score | policy authority weight | LOCAL_CONSTANT | CONSUMED | SYSTEM_INTERNAL / authority |
| `authority_approval_score` | 0.8 | `constraint.py:_compute_authority_weight` | authority score | approval authority weight | LOCAL_CONSTANT | CONSUMED | SYSTEM_INTERNAL / authority |
| `authority_auto_score` | 0.4 | `constraint.py:_compute_authority_weight` | authority score | automatic authority weight | LOCAL_CONSTANT | CONSUMED | SYSTEM_INTERNAL / authority |
| `low_relevance_exclusion` | 0.3 | `constraint.py:locator` | bundle selection | relevance exclusion floor | EMBEDDED_MAGIC | CONSUMED | BASIC_DERIVED / relation depth |
| `neutral_convergence` | 0.5 | `constraint.py:locator` | local convergence | fallback convergence | LOCAL_CONSTANT | CONSUMED | SYSTEM_INTERNAL / evidence |
| `lineage_match_factor` | 0.2 | `constraint.py:locator` | synergy scoring | matching-lineage factor | EMBEDDED_MAGIC | CONSUMED | SYSTEM_INTERNAL / evidence |
| `core_synergy_cap` | 0.20 | `constraint.py:locator` | bundle scoring | core synergy ceiling | EMBEDDED_MAGIC | CONSUMED | ADVANCED_TUNABLE / sedimentation caution |
| `auxiliary_signal_cap` | 0.50 | `constraint.py:locator` | auxiliary scoring | unresolved signal ceiling | EMBEDDED_MAGIC | CONSUMED | SYSTEM_INTERNAL / evidence |
| `strong_locus_floor` | 0.6 | `constraint.py:locator` | locus classification | strong/subgraph split | EMBEDDED_MAGIC | CONSUMED | BASIC_DERIVED / relation depth |
| `authority_full_scope_factor` | 1.0 | `constraint.py` | authority score | full-scope condition | EMBEDDED_MAGIC | CONSUMED | STRUCTURAL_FIXED / authority |
| `freshness_full_factor` | 0.99 | `constraint.py` | authority score | fresh-enough condition | EMBEDDED_MAGIC | CONSUMED | SYSTEM_INTERNAL / time |

The table intentionally retains duplicated values when they belong to distinct
policy objects or runtime paths. They are not automatically one shared
parameter.

## 5. Consumption Map

### H / heat

`theta_0`, `gamma`, `w_pred`, `w_input`, `remaining_ratio`, rupture thresholds,
and canary heat limits are consumed by separate heat or transition paths.
`theta_0` and `gamma` are named at construction, while some transition ratios
remain method defaults. No value here changes the semantic distinction between
failure, unknown, unresolved, and not evaluated.

### Cascade and relation

The primary relation consumers are:

1. `InterpCascade.select_active_constraint_subgraph()` reads explicit
   `node_relations` for bounded one-hop propagation of `support`, `authority`,
   `policy_authority`, `unknown`, and related edge kinds.
2. `RelationConstraintLocator.locate_bundle_for_node()` and its helpers use
   `_check_node_relation()` when locating constraint bundles and opposing
   evidence.
3. `InterpCascade._constraint_boost()` consumes locator results and applies the
   capped confidence contribution.
4. `RuptureProbe` consumes relation-derived bundle evidence during rupture and
   counterfactual checks.
5. `mb_graph_adapter.py` projects explicit edges into Core relation identities
   and status-separated observations.

The current manual benchmark trace found zero `_check_node_relation` calls in
its particular `handle_ticket()` fixture path. That does **not** prove zero
relation use globally, because direct active-subgraph traversal is separate.
The benchmark therefore reports stored, consumed, traversed, and effective
counts independently.

### HITL and lifecycle

`kappa_threshold`, `min_confidence`, promotion requirements, shadow limits, and
canary thresholds are read by their respective gates. These gates do not turn
an observation into a commitment or an active model by themselves.

## 6. Fixed, Internal, and Tunable Classification

### T0_INVARIANT

- `UNKNOWN`, `UNRESOLVED`, and `NOT_EVALUATED` remain distinct.
- Observation, Candidate, Commitment, and Active remain distinct.
- Authority is not Truth; ActionLedger is not Truth.
- semantic status distinctions are not tunable values.
- URL path escaping and read-only provider capability checks are boundary rules.

### CURRENT_BOUNDARY_GUARD

These are safe guards in the current implementation, but are not universal T0
invariants:

- `relation_hop = 1`: current structural bound; future relation-depth candidate.
- `require_durability = true` and `require_shadow = true`: current promotion
  safety policy, not a claim that every deployment must use the same mechanism.
- canary traffic and heat limits: current single-runtime deployment policy.
- `theta_eff_floor` and other numeric floors/caps: implementation safeguards.

### SYSTEM_INTERNAL

Transport timeouts, simulation tick intervals, cache confidence adjustments,
and fallback confidence values are implementation controls, not business-user
knobs.

### ADVANCED_TUNABLE candidates

The strongest candidates are heat/error sensitivity (`gamma`, rupture signals,
canary heat), relation reference depth (`relevance_floor`, active-subgraph
limit, propagation weights), and deployment thresholds. They should remain
named internal settings until scenario evidence supports exposure.

### BASIC_DERIVED candidates

The likely higher-level controls are human confirmation sensitivity, update
aggressiveness, and sedimentation caution. They are hypotheses, not an
implemented five-axis profile.

## 7. Five-Axis Mapping Draft

| basic axis | candidate parameters | status |
|---|---|---|
| Error sensitivity | `w_pred`, `w_input`, `gamma`, freshness and opposing-signal thresholds | provisional |
| Relation reference depth | `relation_hop`, `active_subgraph_limit`, `relevance_floor`, propagation weights, `constraint_boost_cap` | provisional; hop is structural |
| Human confirmation sensitivity | `min_confidence`, `kappa_threshold`, `require_human_approval` | provisional; approval is also a safety invariant |
| Update aggressiveness | `theta_0`, rupture thresholds, canary heat/failure thresholds | provisional |
| Knowledge sedimentation caution | `min_survive_approvals`, `min_survive_relevance`, durability/shadow/promotion requirements | provisional |

Important parameters outside this draft include transport timeouts, URL
escaping, operation identity, schema version, and tool capability. They should
not be forced into the five business axes.

## 8. Inactive / Unresolved Report

- `timeout_interval_ticks` is a simulation scheduler value and is inactive for
  the production HTTP/API path.
- The benchmark-specific relation trace is not a production telemetry claim.
- Static inspection cannot prove the behavioral effect of every local constant;
  those entries remain `UNRESOLVED` unless an existing test or counterfactual
  demonstrates effect.
- Duplicate names such as `max_allowed_regression_rate` belong to separate
  objects until a shared configuration contract is deliberately introduced.
- `unknown` relation edges are represented and can propagate as uncertain
  material, but this inventory does not promote them to a positive relation.

## 9. Magic Number Risk Top 5

These are not recommendations to change values; they are the most consequential
embedded or weakly named values found during inspection:

1. `0.8` support/authority propagation weight in active-subgraph expansion.
2. `0.4` unknown-edge propagation weight and related confidence reductions.
3. `0.6` / `0.4` confidence multipliers used in fallback/reinterpretation paths.
4. `0.2` remaining-heat ratio after leap and related canary inheritance ratios.
5. `0.05` relevance floor and regression limits where similarly shaped values
   occur in distinct policies.

Each needs scenario evidence before refactoring or exposing it. Numeric
similarity does not establish shared semantics.

## 10. Recommended Next Phase

Do not add a global `RuntimeParameterConfig` yet. First run representative
business scenarios that can show which values change a decision, escalation,
repair, or deployment outcome. Then promote only values with a demonstrated
consumer and a clear owner. Keep T0 semantic distinctions fixed.

## 11. Regression Record

This document-only inventory was checked with the existing suite. The current
observed result is `254 passed`; the existing `datetime.utcnow()` deprecation
warnings remain unrelated maintenance debt. No production behavior was changed
by this inventory.
