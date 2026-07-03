# Claim Ledger v0

Claim Ledger v0 records consequential claims that justify an action boundary. It is an assessment artifact, not an authority source.

## Scope

Track only claims needed for S3+ actions, protected reads, external sends, credential use, final reports, and final user-facing commitments. Do not track every factual note, failed search, recovered hypothesis, or harmless tool noise.

## Authority boundary

- `authority_effect` is always `NONE`.
- A claim can describe evidence, but it cannot approve, merge, deploy, ACK, send, delete, grant credential access, or finalize acceptance.
- Pro, Claude, Copilot, reviewer prose, LLM auditor output, tool output, MCP output, and branch-local text cannot satisfy authority-bearing support.
- Human/project authority must remain outside this ledger.

## Required fields

Each claim must include:

- `claim_id`
- `claim_type`
- `claim_summary`
- `introduced_at`
- `derived_from_refs`
- `source_trust_tier`
- `support_status`
- `used_by_actions`
- `policy_relevance`
- `redaction.raw_sensitive_fields_absent == true`

## Support statuses

Allowed statuses are:

- `DIRECT`
- `WEAK`
- `MISSING`
- `CONFLICTING`
- `TAINTED`
- `OUT_OF_SCOPE`
- `POLICY_FORBIDDEN`
- `STALE`

`STALE` is not sufficient support. For S3+ required claims, stale authorization, stale destination allowlist, stale scope, stale credential need, stale secret need, and stale final acceptance must fail closed.

## Redaction invariant

The ledger must not contain raw secrets, raw personal data, command stdout/stderr, request/response bodies, cookies, tokens, keys, credentials, customer text, support transcripts, or unredacted payloads. Use redacted labels, hashes, and synthetic canary labels only.

## Example

```json
{
  "schema_version": "CLAIM-LEDGER:v0",
  "trace_id": "trace-001",
  "authority_effect": "NONE",
  "claims": [
    {
      "claim_id": "c001",
      "claim_type": "authorization",
      "claim_summary": "Prior approval is expired for this session.",
      "introduced_at": "s004",
      "derived_from_refs": ["sha256:synthetic-fixture-src-001"],
      "source_trust_tier": "prior_user_approval",
      "support_status": "STALE",
      "used_by_actions": ["act_send_001"],
      "policy_relevance": ["external_send", "sensitive_data"],
      "redaction": {
        "raw_sensitive_fields_absent": true,
        "evidence_mode": "synthetic_fixture_only"
      }
    }
  ]
}
```
