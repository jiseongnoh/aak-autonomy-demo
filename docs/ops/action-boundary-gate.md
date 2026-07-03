# Action Boundary Gate v0

Action Boundary Gate v0 defines an assessment record for an attempted action boundary. It does not execute tools and does not enforce runtime behavior.

## Non-authority rule

All v0 decisions are labels on an assessment artifact:

- `ALLOW`
- `ALLOW_WITH_WARNING`
- `REQUIRE_MORE_VERIFICATION`
- `REQUIRE_HUMAN_REVIEW`
- `BLOCK`
- `TERMINATE_SESSION`

These labels do not approve, reject, merge, deploy, ACK, send, delete, terminate a live session, or finalize user acceptance. `authority_effect` must be `NONE`.

## Action severity

| Severity | Meaning | Default posture |
|---:|---|---|
| S0 | internal reasoning | log only |
| S1 | public read/search | allow candidate with taint recorded |
| S2 | repo/internal protected read | exact trusted scope required |
| S3 | file write or draft generation | pre-action assessment required |
| S4 | shell/code execution or package install | sandbox/exec policy required |
| S5 | external send, data export, credential use | fail closed; weak support requires review |
| S6 | delete, payment, permission change, deploy, merge | human/project authority required |

## Precedence

When multiple rules apply, choose one label by precedence:

```text
TERMINATE_SESSION > BLOCK > REQUIRE_HUMAN_REVIEW > REQUIRE_MORE_VERIFICATION > ALLOW_WITH_WARNING > ALLOW
```

## Hard fail-closed invariant

For S3+ required claims, these support statuses cannot satisfy support and must not produce allow/accept/final authority:

- `MISSING`
- `CONFLICTING`
- `OUT_OF_SCOPE`
- `POLICY_FORBIDDEN`
- `STALE`

For S5+ actions, `WEAK` support requires at least `REQUIRE_HUMAN_REVIEW`.

## Integration boundary

The schema and policy metadata do not execute tools. The optional deterministic CLI may read fixture JSON and write assessment JSON only; it does not introduce a runtime gate, ToolBroker wrapper, dependency, deployment path, merge gate, or ACK behavior. ToolBroker integration requires a separate approved design.
