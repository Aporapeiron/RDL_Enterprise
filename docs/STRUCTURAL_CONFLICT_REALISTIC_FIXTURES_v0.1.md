# Realistic Structural Conflict Fixtures v0.1

These fixtures model ordinary enterprise conflicts without changing the
conflict detector or adding an urgency policy.

| Case | Relation | SUPPORT context | OPPOSE context | provisional human priority |
| --- | --- | --- | --- | ---: |
| IT-101 | MFA reset without manager approval | incident recovery | security policy | 3 |
| IT-102 | contractor access to production | incident runbook | access policy | 2 |
| IT-103 | preserve user data | legal hold | offboarding policy | 1 |
| IT-104 | restart server now | security advisory | availability rule | 1 |
| IT-105 | disclose account information | recovery procedure | privacy policy | 4 |
| IT-106 | disable user account | security response | business continuity | 2 |

The priority column is a provisional human-review reference only. It is not
fed into `predicted_heat`, `H`, routing, automatic resolution, or promotion.
It exists to make the next observation explicit: the current v0.1 detector
can identify all six structural conflicts, but its current bounded heat
components produce `predicted_heat == 1.0` for each fixture.

## Current Observation

```text
realistic conflict detection       YES
Inbox registration                 YES
provenance                         YES
human priority differentiation     fixture only
current heat differentiation       NOT_ESTABLISHED
```

The difference between the provisional human ordering and the tied heat is
evidence for a future inspection of authority crossing, impact, reversibility,
deadline, and scope. It is not evidence that any one of those factors should
be added to the v0.1 heat formula yet.
