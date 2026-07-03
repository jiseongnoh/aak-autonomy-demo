---
name: peer-review-gate
description: Run a reusable peer-programming review gate with explicit reviewer verdict semantics, claim-altitude checks, negative-evidence regressions, and NEEDS-* handoffs. Use when the user asks for peer programming, sidecar review, "all reviews OK?", or reusable review status tracking.
---

# Peer Review Gate

Use `docs/ops/peer-review-gate.md` in the target repo as the source of truth. If it is not installed,
fall back to this skill and suggest installing/updating Agent Automation Kit.

## Protocol

1. Declare topology:

```text
driver=<codex|claude|human> reviewer=<claude|codex|chatgpt-pro|other> transport=<cmux|local-cli|pro-review-queue|mailbox|manual>
```

2. Build a review bundle with:
   - changed files and diff summary;
   - exact verification commands and output;
   - docs/claim text that could overstate safety;
   - known caveats and non-goals.
3. Require reviewer output in `[PAIR-REVIEW]` format:

```text
[PAIR-REVIEW]
verdict: LGTM | COMMENT | REQUEST_CHANGES | BLOCKED
scope: <files / behavior / command>
evidence: <file:line or command output; mark INFERRED when not verified>
why-it-matters: <customer/runtime/test impact>
minimal-next-step: <fix, test, or decision>
human-gate: none | destructive | deploy | credentials | product decision
```

4. Resolve findings before final status:
   - `LGTM`: still run local verification.
   - `COMMENT`: implement small relevant fixes or record why deferred; rerun focused verification.
   - `REQUEST_CHANGES`: do not say "all reviews OK" until patched and re-reviewed, or say "addressed locally, not re-reviewed".
   - `BLOCKED`: obtain missing evidence or write `NEEDS-*`.
5. Apply the claim-altitude gate:
   - partial/subset verifier is not complete proof;
   - deterministic smoke is not e2e;
   - optimization is not the secure default;
   - reviewer output is advisory, not approval/merge/deploy/ACK authority.
6. For security/privacy/evaluator claims, add a negative-evidence regression for any known uncovered case.
7. Final report must include reviewer matrix, changed files, verification commands, verified vs not-proven claims, and handoff lines.

## Answering "all reviews OK?"

Say "yes" only if every configured reviewer is `LGTM` or `COMMENT/no blocker` after accepted findings
were resolved and verified. If an earlier reviewer returned `REQUEST_CHANGES` and was not re-run, say:

```text
Not exactly: latest peer review is no-blocker, and requested fixes were addressed locally, but the earlier REQUEST_CHANGES reviewer has not re-reviewed this patch.
```
