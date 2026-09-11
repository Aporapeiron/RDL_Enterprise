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

Unknown, unresolved, and not-evaluated inputs are not converted into a
structural conflict by this inbox. A conflict must be explicitly observed by
an upstream inspection.

## Boundary

The inbox is in-memory and Enterprise-local in v0.1. Human routing records the
authenticated actor in history but does not itself authorize a business
decision. Persistence, multi-writer coordination, automatic resolution, and
urgency policy remain outside this boundary.
