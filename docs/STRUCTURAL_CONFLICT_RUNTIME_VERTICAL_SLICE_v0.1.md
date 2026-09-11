# Structural Conflict Runtime Vertical Slice v0.1

This document records the current bounded integration of structural conflict
inspection into `EnterpriseRuntime`.

## Product Flow

```text
BusinessInput
-> normal interpretation
-> active relation profiles for this case
-> detect_relation_profile_conflicts
-> StructuralConflictInbox
-> scalar fields on the ticket result
-> human list/detail/decision read path
```

`EnterpriseRuntime` accepts an optional `relation_profile_provider`. The
provider is the current Enterprise-local boundary for forming the active
profiles; the runtime does not scan every `M_B` node. The provider receives
the input, prediction, and active graph and must return a mapping from
structure id to existing `RelationConstraintProfile` values.

## Result Boundary

Dispatch and synchronous execution results expose only:

```text
structural_conflict_status
structural_conflict_count
predicted_conflict_heat
```

Detailed structure ids, heat components, support evidence, provenance, and
human decision history remain in the in-memory `StructuralConflictInbox`.

## Meaning Constraints

```text
STRUCTURAL_CONFLICT != failure
STRUCTURAL_CONFLICT != Truth
predicted_heat != actual H
support_strength != automatic resolution
Authority != Truth
UNKNOWN / UNRESOLVED / NOT_EVALUATED are not conflicts
```

Conflict inspection copies the active profile mapping before inspection and
does not mutate the pre-update `M_B`. An explicit opposite polarity for the
same semantic relation is required. Same-polarity relations, different
semantic keys, and unresolved polarity remain outside the conflict inbox.

Existing `human_only`, `require_approval`, and irreversible safety gates are
unchanged; structural conflict detection does not replace them or auto-select
a winning structure.

## Inbox Read Path

`runtime.conflict_inbox.list_cases()` returns case summaries ordered by total
predicted heat. `detail(case_id)` expands the individual observations.
`route_to_human()` and `record_decision()` append authenticated human events
without rewriting the original observation. Counterfactual comparison also
leaves the inbox unchanged.

## v0.1 Boundary

The inbox is Enterprise-local, in-memory, and single-runtime. Conflict
observation is not persisted in SQLite in this slice. Automatic conflict
resolution, urgency policy, interaction/overlap heat, predicted-heat
learning, and multi-writer coordination remain outside this boundary.

The `relation_profile_provider` and compatibility/profile formation logic are
explicit integration points. They are not promoted into generic RDL Core by
this slice.
