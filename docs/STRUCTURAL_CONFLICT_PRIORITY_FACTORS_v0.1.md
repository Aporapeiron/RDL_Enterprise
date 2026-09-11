# Structural Conflict Priority Factors v0.1

This is an observation sheet for explaining provisional human review order.
It is not a heat formula, urgency policy, routing rule, or automatic resolver.

The factor labels below are hypotheses for the next inspection boundary. They
must be validated against actual operators and cases before becoming runtime
parameters.

| Case | Provisional priority | Impact scope | Reversibility | Deadline pressure | Authority crossing | Data sensitivity | Continuity impact |
| --- | ---: | --- | --- | --- | --- | --- | --- |
| IT-101 | 3 | individual account | reversible with review | medium | manager/security | medium | medium |
| IT-102 | 2 | production environment | potentially high-impact | high during incident | vendor/security | high | high |
| IT-103 | 1 | legal evidence and device | destructive if initialized | legal deadline | legal/IT | high | low |
| IT-104 | 1 | production server | service disruption | critical vulnerability | security/operations | low | high |
| IT-105 | 4 | individual account data | disclosure is hard to undo | low | support/privacy | high | low |
| IT-106 | 2 | user/service access | reversible but disruptive | high during suspected breach | security/business | medium | high |

## Interpretation Boundary

The table explains why a human might order cases differently even when the
current detector reports `predicted_heat == 1.0` for all of them. It does not
claim that the displayed values are authoritative measurements. In
particular:

```text
human priority != predicted_heat
factor hypothesis != policy
authority crossing != truth
high impact != automatic emergency
irreversibility != automatic resolution
```

The next evaluation should collect operator rationale and concrete case
outcomes for each factor. Until then, the factors remain Enterprise-local
inspection material and do not alter `H`, `support_strength`, routing,
promotion, or the bounded v0.1 heat aggregation.
