# Structural Conflict Inbox v0.1

This is an Enterprise-local read model for inspecting explicitly observed
structural conflicts. It does not resolve a conflict, mutate `M_B`, promote a
candidate, or decide truth, risk, or urgency.

## Flow

```text
structural inspection
-> STRUCTURAL_CONFLICT
-> predicted_heat and structure ids
-> case aggregation
-> descending inbox
-> detail / human review
```

`predicted_heat` is a bounded inspection signal, not actual `H`, risk truth,
or an emergency priority. The v0.1 aggregation method is
`bounded_sum_v0_1`: each conflict is bounded to 0..1 and a case total is the
finite sum. Interaction and overlap correction are not evaluated yet.

`support_strength` is retained as supporting evidence only. It does not clear
or resolve a structural conflict. Observation, candidate, commitment, and
active state remain separate.

The `observe_conflict` entry point requires the upstream inspection to mark
the pair as explicitly incompatible. It calculates `predicted_heat` from the
finite component values and does not infer a conflict from support, authority,
or similarity alone. Human decisions are appended to history; they do not
rewrite the original conflict observation. A counterfactual can select a
different structure composition for comparison without mutating the inbox.

`detect_active_conflicts` is the minimal upstream bridge: it enumerates pairs
of active structures and accepts only an explicit incompatibility result from
the compatibility inspection. An unknown result is skipped, not converted
to a conflict or failure.

Unknown, unresolved, and not-evaluated inputs are not converted into a
structural conflict by this inbox. A conflict must be explicitly observed by
an upstream inspection.

## Boundary

The inbox is in-memory and Enterprise-local in v0.1. Human routing records the
authenticated actor in history but does not itself authorize a business
decision. Persistence, multi-writer coordination, automatic resolution, and
urgency policy remain outside this boundary.
