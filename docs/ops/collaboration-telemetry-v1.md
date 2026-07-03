# Collaboration telemetry v1

`COLLAB-EVENT:v1` is the minimal evidence contract for human-agent collaboration metrics. It is a non-authoritative projection input: it helps report work, time, automation mode, and collaboration quality, but it never grants ACK, approval, merge, deploy, or final acceptance.

## Design rules

- GitHub remains the governance authority for issues, claims, ACKs, reviews, and acceptance.
- Runtime spans are execution evidence, not governance authority.
- Metrics projections are rebuildable cache, not source of truth.
- Raw PR/issue bodies, secrets, browser state, webhook URLs, or private documents must not be copied into telemetry events.
- Untrusted display strings must stay display-only. They must never become routing, execution, or approval signals.
- Missing fields remain `unknown`; do not infer agent identity or human effort from prose.

## Event envelope

Every event is one JSON object per line:

```json
{
  "schema": "COLLAB-EVENT:v1",
  "event_id": "repo:unique-event-id",
  "task_id": "repo:issue-123",
  "event_type": "agent_run_started",
  "occurred_at": "2026-06-22T00:00:00Z",
  "observed_at": "2026-06-22T00:00:01Z",
  "actor": {
    "type": "agent",
    "agent_id": "codex-local-01",
    "node_id": "mac-mini-01"
  },
  "mode": {
    "initiation": "human_ack",
    "execution": "bounded_autonomous",
    "final_authority": "human"
  },
  "source": {
    "system": "agent_runtime",
    "record_id": "run-abc",
    "integrity": "verified"
  },
  "authority": "observation_only",
  "correlation_id": "run-abc"
}
```

Required top-level fields:

- `schema`: exactly `COLLAB-EVENT:v1`
- `event_id`: stable unique id; duplicate ids are ignored by calculators
- `task_id`: stable work item id; this is the denominator for completed work
- `event_type`: one of the event types below
- `occurred_at`: RFC3339 UTC timestamp ending in `Z`
- `actor`: object with `type`; agent events should include `agent_id`
- `source`: object with `system`, `record_id`, and `integrity`
- `authority`: must be `observation_only` unless a separate reviewed authority contract says otherwise

Recommended fields:

- `observed_at`: when the local observer saw the event
- `mode`: raw automation axes
- `correlation_id`: run/span id for start-finish matching
- `task`: optional metadata such as `type`, `complexity`, or `repo`; no raw private body
- `outcome`: optional terminal metadata such as `accepted`, `cancelled`, `defect`, `reverted`

## Event types

Minimum lifecycle:

- `task_created`
- `agent_run_started`
- `agent_run_finished`
- `human_review_started`
- `human_accepted`
- `task_cancelled`

Optional later events:

- `human_intervention`
- `changes_requested`
- `agent_reassigned`
- `defect_reported`
- `reverted`
- `cost_recorded`

## Automation mode axes

Do not store only `manual`, `semi`, or `full`. Store three raw axes and derive labels in the report:

- `initiation`: `human_triggered`, `human_ack`, `policy_preauthorized`, or `unknown`
- `execution`: `suggestion_only`, `bounded_write`, `bounded_autonomous`, or `unknown`
- `final_authority`: `human`, `automated`, or `unknown`

Derived UI labels:

- `manual`: human-triggered + suggestion-only + human final authority
- `semi_automatic`: human-triggered/human_ack + bounded_write or bounded_autonomous + human final authority
- `fully_automatic_execution`: policy-preauthorized + bounded_autonomous + human final authority
- `auto_final_authority`: any event whose final authority is automated; this is outside the current kit safety boundary
- `unknown`: insufficient axes

The current kit's safety boundary keeps final authority human. Therefore “fully automatic” in reports must mean execution automation only, not auto-merge or auto-acceptance.

## Minimal metrics

- `completed_tasks`: distinct `task_id` with `human_accepted`, excluding cancelled tasks
- `cycle_seconds`: `human_accepted.occurred_at - task_created.occurred_at`
- `agent_active_seconds`: sum of matched `agent_run_started` to `agent_run_finished` spans by `correlation_id`
- `human_wait_seconds`: first `human_review_started - latest prior agent_run_finished` when both exist
- `agent_completed_tasks`: accepted tasks attributed to agents with verified finished runs on that task
- `mode_counts`: accepted task count by derived mode label
- `unknown_counts`: events or tasks with missing/invalid evidence

Do not present PR count, Teams card count, or event count as completed work. The completed-work denominator is `task_id` with a terminal accepted state.
