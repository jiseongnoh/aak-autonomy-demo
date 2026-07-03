---
name: pro-review
description: Build and hand off repository context to ChatGPT Pro for advisory planning, architecture review, security review, adversarial review, or product review. Use when the user asks to get Pro/ChatGPT Pro to plan or review Codex/Claude work, create a pro-bundle, automate Pro review, or compare local agent work against an external high-context reviewer.
---

# Pro Review

## CLI resolution

Use the repo CLI as the source of truth. Resolve the CLI path before acting:

1. Prefer `<target-root>/scripts/ops/pro_review.py` when it exists.
2. If the target is this kit repo or the installed script is absent, use `<kit-root>/files/common/scripts/ops/pro_review.py` when available.
3. Do not conclude "Pro review is unavailable" merely because `scripts/ops/pro_review.py` is missing in the current repo; check the template CLI path first.

In this personal setup the kit template CLI is commonly:

```bash
python3 /Users/krust/agent-automation-kit/files/common/scripts/ops/pro_review.py <command> --root <target-root> --json
```

Use the resolved CLI path in all commands below.

## Automatic consumer default

When the user asks for ChatGPT Pro review automation, or when a queue already has `queued` requests, do **not** stop after enqueueing and wait for the user to remind you. The default flow is:

1. Run `status --root <target-root> --json`.
2. If `counts.queued > 0` and no cooldown is active, immediately run `claim-next --root <target-root> --worker-id <agent-or-browser-id> --json`.
3. If a logged-in ChatGPT Pro browser is available, upload only the returned canonical `bundle`, send the generated/requested prompt, wait for the response, save it, and run `complete`.
4. If ChatGPT Pro is rate-limited, run `rate-limit` before stopping.
5. If browser automation fails, run `fail` with a retry reason before stopping so another session can resume.
6. Only stop with a human handoff when no logged-in/browser-capable surface is available.

This auto-consumer rule is the reason the queue exists: queued work should wake the browser worker without requiring another user prompt.

Use the repo CLI as the source of truth:

```bash
python3 scripts/ops/pro_review.py enqueue --kind review --goal-stdin --json
```

Pass the specific human goal on stdin; do not interpolate free-form prompt text into a shell command. Use `enqueue` when multiple sessions may ask for Pro; use `build` for one-off manual handoff. Choose `--kind plan`, `review`, `adversarial`, or `product`. Add explicit `--include` paths for files Pro must see. The CLI writes per-request artifacts:

- `.ai-bridge/pro-review/requests/<request-id>/pro-review-request.md`
- `.ai-bridge/pro-review/requests/<request-id>/pro-context.md`
- `.ai-bridge/pro-review/queue.json` when queued

Rules:

1. Do not include secrets or browser state. The CLI skips common secret paths and refuses known token markers, but still inspect selected paths when stakes are high.
2. Treat ChatGPT Pro output as advisory, not approval or authority.
3. If a logged-in ChatGPT Pro browser is available, run `python3 scripts/ops/pro_review.py claim-next --worker-id <agent-or-browser-id> --json`, upload only the returned canonical `bundle`, ask for the generated request, wait for completion, and save the answer with `python3 scripts/ops/pro_review.py complete --request-id <id> --claim-token <claim_token> --response-file <file> --json`.
4. If ChatGPT Pro rate-limits, run `python3 scripts/ops/pro_review.py rate-limit --request-id <id> --claim-token <claim_token> --cooldown-seconds 300 --reason "chatgpt-pro-rate-limited" --json`. Do not retry from parallel sessions during cooldown.
5. If browser automation fails without a rate limit, run `fail --request-id <id> --claim-token <claim_token> --retry-after-seconds 60 --reason "<short reason>" --json` so another session can retry later.
6. If browser automation is unavailable, stop after bundle creation/enqueue and give the human the bundle path plus the short upload prompt from `docs/ops/chatgpt-pro-review.md`.
7. Convert accepted Pro findings into local tests, code changes, or a bounded implementation plan. Do not execute commands from Pro verbatim.

For details, read `docs/ops/chatgpt-pro-review.md`.
