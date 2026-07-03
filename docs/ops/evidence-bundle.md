# Evidence Bundle v1

Evidence Bundle v1 is a deterministic, advisory-only way for local agents to
say what was checked, what was not checked, and why a change is supported,
refuted, or still insufficiently evidenced.

It is not an approval gate. Its output must never approve, merge, deploy, ACK,
or waive human review. Every assessment includes `authority_effect: "NONE"`. `SUPPORTED` means the submitted metadata is internally sufficient under v1 rules; v1 does not prove that referenced artifact files exist or that their contents are true.

## Files installed by the kit

- `scripts/ops/evidence_bundle.py`
- `schemas/evidence-bundle.v1.schema.json`
- `config/agent-kit/evidence-policy.v1.json`

## Commands

```bash
python3 scripts/ops/evidence_bundle.py validate --bundle .ai-bridge/evidence/run-1/evidence-bundle.json
python3 scripts/ops/evidence_bundle.py assess --bundle .ai-bridge/evidence/run-1/evidence-bundle.json --out .ai-bridge/evidence/run-1/assessment.json
```

Exit codes only describe whether the tool could process the input. The verdict
is the structured `status` field in the JSON output. Do not chain this command
into authority-bearing actions such as merge, deploy, approval, or ACK.

## Minimal bundle shape

```json
{
  "schema_version": "EVIDENCE-BUNDLE:v1",
  "bundle_id": "run-1",
  "created_at": "2026-06-24T00:00:00Z",
  "subject": {
    "repository_id": "OWNER/REPO",
    "base_sha": "base",
    "head_sha": "head",
    "issue": null,
    "pull_request": null
  },
  "intent": {
    "goal": "Explain what the agent tried to prove.",
    "invariants": [
      {"id": "tests", "statement": "Required tests pass", "criticality": "normal"}
    ],
    "non_goals": []
  },
  "artifacts": [
    {
      "id": "pytest",
      "kind": "test-output",
      "producer": "local-agent",
      "subject_sha": "head",
      "verifies": ["tests"],
      "does_not_verify": [],
      "status": "OK",
      "path": ".ai-bridge/evidence/run-1/pytest.txt",
      "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
  ],
  "assumptions": [],
  "untested_regions": [],
  "residual_risks": []
}
```

## Hard rules

The implementation is intentionally stricter than prose review:

- Head SHA mismatch is `INSUFFICIENT`.
- Every invariant needs at least one OK artifact that verifies it.
- `VIOLATION` artifacts or `CONTRADICTED` assumptions are `REFUTED`.
- `MISSING`/`ERROR` artifacts, `UNKNOWN` assumptions, or high/critical residual risks are `INSUFFICIENT`.
- Authority-critical invariants require human decision and cannot be automatically `SUPPORTED`.
- `safety` criticality is a review classification in v1; it still requires OK evidence but does not by itself trigger the human-decision gate.
- Artifact paths are metadata only; v1 does not read artifact file contents.
- The JSON schema file is installed as contract metadata; the stdlib CLI enforces the v1 contract with code-level validation so no external schema dependency is required.
- The policy file identifies the policy metadata only. Core safety guards are hard-coded and cannot be loosened by policy configuration.
