# Claude advisory PR review fallback

Status: optional advisory review fallback for manual-local-v1.

This document defines the Claude advisory review workflow for requesting Claude
Code review when GitHub Copilot review is unavailable, delayed, or returns a
service-error review. The reviewed template is kept at
`docs/orchestration/review-guides/claude-advisory-review.workflow.yml`, and the
active workflow path is `.github/workflows/claude-advisory-review.yml`. It is
not a replacement for Human Release Manager approval.

## What this uses

The workflow uses `anthropics/claude-code-action` with a Claude Code subscription
OAuth token:

```yaml
claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

It does **not** require committing an Anthropic API key. The secret is still a
credential and must be stored only in GitHub Actions secrets.

## Activation status

This workflow becomes available after this PR is merged to the default branch.
A GitHub actor/token with `workflow` scope is required to update the active
`.github/workflows/claude-advisory-review.yml` file.

## One-time setup

1. On a local machine where Claude Code is logged into a Claude Pro, Max, Team,
   or Enterprise subscription, run:

   ```bash
   claude setup-token
   ```

2. Copy the generated token.
3. In GitHub, open repository settings:
   `Settings -> Secrets and variables -> Actions -> New repository secret`.
4. Add:

   ```text
   Name: CLAUDE_CODE_OAUTH_TOKEN
   Value: <token from claude setup-token>
   ```

Do not paste this token in issues, PRs, comments, workflow logs, Teams, or repo
files.

## How to request review

### PR comment trigger

On a PR, a repository owner/member/collaborator can comment:

```text
@claude-review please review this PR for correctness, UX regressions,
privacy/security scope, and protocol compliance.
```

### Manual workflow trigger

After merge, run the `claude-advisory-review` workflow from GitHub Actions and provide the PR
number. This is useful when the PR comment trigger is noisy or unavailable.

## Authority boundary

Claude review is advisory only.

Claude may:

- read the PR context;
- leave review feedback/comments;
- summarize findings for humans;
- produce a `CLAUDE-REVIEW:v1` marker block.

Claude must not:

- edit files;
- create branches or commits;
- open/update PRs;
- approve, merge, final-accept, or waive gates;
- access secrets beyond the workflow-provided OAuth token;
- replace Copilot, Sentinel, node acceptance, or Human Release Manager final
  decision.

If Claude review is used because Copilot is unavailable, the PR still needs a
current-head `COPILOT-CONSIDERED:v1` waiver or successor human-gate marker when
required by the active protocol.

## Korean operator note

이 워크플로우는 API key를 repo에 넣는 방식이 아니라 Claude subscription에서
`claude setup-token`으로 만든 OAuth token을 GitHub Secret에 넣어 사용하는
방식입니다. 그러나 token은 여전히 민감정보입니다.

Claude 리뷰 결과는 참고용입니다. Claude가 approve/merge/final acceptance/waiver
권한을 갖는 것이 아니며, 최종 판단은 Human Release Manager가 GitHub에 기록해야
합니다.

## Suggested marker

```json
{
  "schema": "CLAUDE-REVIEW-CONSIDERED:v1",
  "repo": "jiseongnoh/aak-autonomy-demo",
  "pr": 0,
  "target_head_sha": "...",
  "claude_review_urls": [],
  "advisory_only": true,
  "api_key_used_by_repo": false,
  "auth_method": "CLAUDE_CODE_OAUTH_TOKEN",
  "findings_actioned": [],
  "findings_deferred": [],
  "verdict": "complete|needs_changes|blocked",
  "reviewed_by": "human-release-manager-or-independent-verifier",
  "created_at": "<iso8601>",
  "marker_sha256": "..."
}
```
