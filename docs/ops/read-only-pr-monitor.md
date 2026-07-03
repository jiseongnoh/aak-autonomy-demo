# Read-only PR monitor

This monitor is a bounded local observer for GitHub pull requests. It exists to
make new PRs and review workflow results visible to a local agent session without
turning the agent into a remote unattended runner.

## What it does

- polls `gh pr list` every 1-3 minutes by default;
- inspects each open PR with `gh pr view` for review workflow status and recent reviews;
- writes local evidence only:
  - `.omc/state/read-only-pr-monitor/heartbeat.json`
  - `.omc/state/read-only-pr-monitor/latest.json`
  - `.omc/state/read-only-pr-monitor/events.jsonl`
  - `.omc/state/read-only-pr-monitor/errors.jsonl`
- optionally sends a local macOS notification with `--notify`;
- optionally prepares an issue-comment body locally, and posts it only when both
  `--issue-comment-target` and `--post-issue-comments` are explicitly supplied.

## What it must not do

The monitor must never:

- merge, approve, auto-ACK, auto-enter, claim, push, switch branches, or mutate PR branches;
- trigger GitHub workflows or review workflows;
- treat Copilot/Claude/CI output as final approval;
- grant implementation, release, merge, waiver, or permission authority;
- run as a hidden daemon. Start it deliberately, and stop it with Ctrl-C or a supervisor you control.

## Run locally

```bash
python3 scripts/ops/read_only_pr_monitor.py \
  --repo jiseongnoh/aak-autonomy-demo \
  --interval 120 \
  --notify
```

One-cycle smoke check:

```bash
python3 scripts/ops/read_only_pr_monitor.py \
  --repo jiseongnoh/aak-autonomy-demo \
  --once \
  --emit-existing
```

Optional issue ledger comment, disabled by default:

```bash
python3 scripts/ops/read_only_pr_monitor.py \
  --repo jiseongnoh/aak-autonomy-demo \
  --interval 120 \
  --issue-comment-target 123 \
  --post-issue-comments
```

If `--issue-comment-target` is supplied without `--post-issue-comments`, the
monitor only writes a prepared markdown comment under local state.

## Hotel Concierge precedent

Hotel Concierge runs a stronger local Control Tower watcher under
`~/Library/Application Support/hotel-concierge-control-tower/`. The key lesson
ported here is narrower than that system:

- watcher produces observation events;
- separate review/worker components must consume every queued `kind`, or health
  must report unconsumed kinds;
- peer-review visibility is evidence, not merge authority.

This kit ships only the safe reusable subset: PR observation + review workflow
status logging. Any semantic review generator, worker queue, or tmux injector must
remain a separate, explicitly reviewed component.

## Health checks

A healthy monitor has:

- a recent `heartbeat.json` timestamp;
- advancing `cycle` count;
- `events.jsonl` entries when new PR heads or review workflow status changes;
- no persistent `poll_error` entries in `errors.jsonl`.

If the monitor is used as a background process, keep it bounded and visible. Do
not use it as proof of review completion unless the relevant PR/head SHA has a
review evidence marker in GitHub and a human release manager has made the final decision.
