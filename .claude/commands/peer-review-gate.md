# Peer Review Gate

Use this command when asked to peer program, review another agent's work, or answer whether all reviews are OK.

Follow `docs/ops/peer-review-gate.md` when present. Return exactly this shape:

```text
[PAIR-REVIEW]
verdict: LGTM | COMMENT | REQUEST_CHANGES | BLOCKED
scope: <files / behavior / command>
evidence: <file:line or command output; mark INFERRED when not verified>
why-it-matters: <customer/runtime/test impact>
minimal-next-step: <fix, test, or decision>
human-gate: none | destructive | deploy | credentials | product decision
```

Review checklist:

1. Verify the actual diff and command output; mark anything not checked as INFERRED.
2. Challenge claim altitude: partial verify vs complete proof, smoke vs e2e, optimization vs default.
3. Ask for negative-evidence tests when known gaps are represented only in prose.
4. Treat your output as advisory evidence, not approval, merge, deploy, ACK, waiver, or final acceptance.
5. If you return `REQUEST_CHANGES`, include the smallest test or doc/code change that would resolve it.
