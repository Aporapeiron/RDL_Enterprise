# Interaction Reflection Plan v0.2

Status: implementation in progress; acceptance not established.
Semantic reference: T0 BASE / SPEC v2.1 (interaction and action sections).

## Operational model

An EFP is a bounded section of interaction. A response may change the
conditions generating the next EFP. Compare F and F' using the same frozen,
pre-update M_B; retain only unresolved observed discrepancy through the
existing E-to-H path. Generative conditions may be inspected on demand (C10).

The earlier proposal to add E_structural to temporal E is deprecated and
not adopted. Structural conflict and predicted heat remain upstream
observations. Neither conflict heat nor shadow scores directly feed H.
Rejected structures may be inspected through selective, effect-free shadow
evaluation rather than continuous tracking of every alternative.

## Delivery and evidence

| Phase | Required work | Acceptance evidence |
| --- | --- | --- |
| P0 | Synchronize BASE/SPEC v2.1 design | This plan; no structural-E addition |
| P1 | Static conflict observation | Detection alone leaves H unchanged |
| P2 | Interaction provenance in case trace | Structures used and actual response recoverable |
| P3 | Subsequent EFP comparison | F and F' use identical pre-update context |
| P4 | Link conflict, selection, response and E | Provenance records possible association, not proven causality |
| P5 | Selective shadow | Persistent, important or uncertain conflicts can request effect-free alternative evaluation |
| P6 | Reinspect EFP generation | On-demand inspection of prior response, other structures and environment |
| P7 | Existing residual-to-H path | Conflict and shadow scores cannot add H directly |
| P8 | Human attention gate | Actionable intervention, appropriate authority and persistence or safety requirement; deduplication and change-point aggregation |
| P9 | Regression and scenario acceptance | Six realistic fixtures plus continuing interactions; no spurious H or notifications; CI green |
| P10 | Operational observation | Real case provenance from conflict through response and subsequent EFP/E/H |

P2-P9 remain subject to the acceptance evidence above. P10 now has a live
partial observation: a real Atlassian Jira lookup was followed by a second
real lookup recorded as EFP', then evaluated with the same frozen pre-update
M_B to produce F' and E/H. This does not yet establish a real structural
conflict-to-response chain; no conflict is inferred or fabricated from the
live Jira record.

## Human attention

Observation is not notification. Conflict is not a review request. Increased
H alone does not immediately escalate. Human attention/review load is a
separate Enterprise-local quantity. Ten repeated observations should aggregate
into one eligible review request, with meaningful changes recorded separately.
Mandatory existing authority and safety gates remain effective.

## Evidence integrity

The existing six realistic cases are synthetic. Priority labels and factor
tables authored during development are hypotheses, not collected operator
judgments. Real operator rationale and real subsequent observations must be
collected before claiming P10 or calibrating heat/priority from those labels.
Missing observations remain missing; no synthetic outcome substitutes for a
live operational observation.
