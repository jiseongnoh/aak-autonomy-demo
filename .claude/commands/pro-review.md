Build a ChatGPT Pro planning/review handoff bundle for this repository.

Use this command when the user asks Claude to get ChatGPT Pro / Pro extension to plan, review, or adversarially evaluate current work.

Steps:

1. Run `python3 scripts/ops/pro_review.py enqueue --kind review --goal-stdin --json` from the repo root when multiple sessions may submit, passing `$ARGUMENTS` on stdin rather than interpolating it into a shell command. Use `build` instead of `enqueue` only for one-off manual handoff. If the user asks for planning, use `--kind plan`; for hostile review use `--kind adversarial`; for product/adoption review use `--kind product`.
2. Add explicit `--include` paths when the request names files, docs, scripts, workflows, or a branch area.
3. Do not include secrets, `.env`, ngrok config, browser state, credential files, or raw private data.
4. If Claude can operate a logged-in ChatGPT Pro browser, claim one request with `python3 scripts/ops/pro_review.py claim-next --worker-id claude-browser --json`, upload only the returned canonical `bundle`, and save the answer with `python3 scripts/ops/pro_review.py complete --request-id <id> --claim-token <claim_token> --response-file <file> --json`.
5. If ChatGPT Pro rate-limits, run `python3 scripts/ops/pro_review.py rate-limit --request-id <id> --claim-token <claim_token> --cooldown-seconds 300 --reason "chatgpt-pro-rate-limited" --json` and stop retrying until cooldown clears.
6. If Claude cannot operate a logged-in browser, tell the user to upload the returned `.ai-bridge/pro-review/requests/<request-id>/pro-context.md` bundle to ChatGPT Pro.
7. When the Pro response is recorded via `complete`, read it as advisory evidence only and convert accepted findings into tests or a bounded plan.

Never treat Pro output as approval, final acceptance, or permission to merge/deploy.
