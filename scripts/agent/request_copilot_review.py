#!/usr/bin/env python3
"""Request GitHub Copilot code review for a pull request via GitHub CLI.

Official request path:

    gh pr create --reviewer @copilot
    gh pr edit PR_NUMBER --add-reviewer @copilot

This helper enforces a minimum gh version, runs the documented CLI command,
and verifies the actual PR review-request state through GraphQL so bot reviewers
such as `copilot-pull-request-reviewer` are not missed by simplified views.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import textwrap
import time
import hashlib
from dataclasses import dataclass
from typing import Any

COPILOT_REVIEWER = "@copilot"
COPILOT_BOT_LOGINS = {"copilot-pull-request-reviewer"}
COPILOT_ERROR_PATTERNS = (
    "copilot encountered an error",
    "unable to review this pull request",
)
MIN_GH_VERSION = (2, 88, 0)
DEFAULT_GH_TIMEOUT_SECONDS = 120
REPO_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


def run(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int = DEFAULT_GH_TIMEOUT_SECONDS,
) -> CommandResult:
    try:
        proc = subprocess.run(args, text=True, capture_output=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            args=args,
            returncode=124,
            stdout=exc.stdout or "",
            stderr=f"Command timed out after {timeout}s: {exc}",
        )
    except OSError as exc:
        return CommandResult(
            args=args,
            returncode=127,
            stdout="",
            stderr=f"Failed to execute command: {exc.__class__.__name__}: {exc}",
        )
    return CommandResult(args=args, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def compact(text: str, limit: int = 4000) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def parse_gh_version(version_output: str) -> tuple[int, int, int] | None:
    match = re.search(r"gh version (\d+)\.(\d+)\.(\d+)", version_output)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def version_text(version: tuple[int, int, int] | None) -> str:
    if version is None:
        return "unknown"
    return ".".join(str(part) for part in version)


def version_at_least(actual: tuple[int, int, int] | None, minimum: tuple[int, int, int]) -> bool:
    return actual is not None and actual >= minimum


def valid_repo_full_name(repo: str) -> bool:
    parts = repo.split("/")
    return len(parts) == 2 and all(REPO_SEGMENT_RE.fullmatch(part) for part in parts)


def normalize_repo_full_name(repo: str) -> str:
    return repo.strip()


def likely_remediation(
    stderr: str,
    stdout: str,
    gh_version: str,
    minimum_version: tuple[int, int, int],
) -> list[str]:
    text = f"{stderr}\n{stdout}".lower()
    actual_version = parse_gh_version(gh_version)
    hints: list[str] = []
    if "failed to execute command" in text or actual_version is None:
        hints.append("Install GitHub CLI or ensure `gh` is executable on PATH.")
    elif not version_at_least(actual_version, minimum_version):
        hints.append(f"Upgrade GitHub CLI to >= {version_text(minimum_version)}; installed is {version_text(actual_version)}.")
    if "resource not accessible" in text or "403" in text or "permission" in text:
        hints.append("Check token permissions: contents:read and pull-requests:write are required; issues:write is needed for failure comments.")
    if "not found" in text or "could not resolve" in text or "unknown" in text:
        hints.append("Confirm PR number/repo and that gh recognizes @copilot as a special reviewer.")
    if "copilot" in text and ("enable" in text or "not enabled" in text or "disabled" in text):
        hints.append("Enable Copilot code review or automatic Copilot review in repository/organization settings.")
    if not hints:
        hints.append("Verify gh version, token scope, repository Copilot settings, PR draft state, and existing Copilot review request state.")
    return hints


def gh_api_graphql_page(
    repo: str,
    pr: int,
    env: dict[str, str],
    *,
    reviews_before: str | None = None,
) -> tuple[CommandResult, dict[str, Any] | None]:
    owner, name = repo.split("/", 1)
    query = """
query($owner: String!, $repo: String!, $number: Int!, $reviewsBefore: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      headRefOid
      headRefName
      isDraft
      headRepository { nameWithOwner }
      baseRepository { nameWithOwner }
      reviewRequests(first: 100) {
        nodes {
          requestedReviewer {
            __typename
            ... on User { login }
            ... on Bot { login }
            ... on Team { slug name organization { login } }
          }
        }
      }
      reviews(last: 100, before: $reviewsBefore) {
        pageInfo {
          hasPreviousPage
          startCursor
        }
        nodes {
          author { login }
          state
          url
          submittedAt
          body
          commit { oid }
        }
      }
    }
  }
}
"""
    result = run(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"owner={owner}",
            "-f",
            f"repo={name}",
            "-F",
            f"number={pr}",
            "-f",
            f"query={query}",
        ]
        + ([] if reviews_before is None else ["-f", f"reviewsBefore={reviews_before}"]),
        env=env,
    )
    if result.returncode != 0:
        return result, None
    try:
        return result, json.loads(result.stdout)
    except json.JSONDecodeError:
        return result, None


def gh_api_graphql(
    repo: str,
    pr: int,
    env: dict[str, str],
    *,
    max_review_pages: int = 5,
) -> tuple[CommandResult, dict[str, Any] | None]:
    """Fetch PR activity, paging older reviews until current-head Copilot activity is found.

    The first page is enough for ordinary PRs. Pagination prevents long review
    loops from hiding a current-head Copilot review outside the newest 100
    reviews. Review requests are available on every page response, so only the
    reviews collection is merged.
    """
    result, data = gh_api_graphql_page(repo, pr, env)
    if result.returncode != 0 or data is None:
        return result, data

    pages_scanned = 1
    pr_obj = pull_request(data)
    if not pr_obj:
        return result, data

    while pages_scanned < max(max_review_pages, 1):
        activity = copilot_activity(data)
        reviews = (pr_obj.get("reviews") or {})
        page_info = reviews.get("pageInfo") or {}
        start_cursor = page_info.get("startCursor")
        if activity.get("reviews") or not page_info.get("hasPreviousPage") or not start_cursor:
            break

        older_result, older_data = gh_api_graphql_page(
            repo,
            pr,
            env,
            reviews_before=str(start_cursor),
        )
        if older_result.returncode != 0 or older_data is None:
            return older_result, None

        older_pr = pull_request(older_data) or {}
        older_reviews = ((older_pr.get("reviews") or {}).get("nodes") or [])
        reviews.setdefault("nodes", [])
        reviews["nodes"].extend(older_reviews)
        reviews["pageInfo"] = (older_pr.get("reviews") or {}).get("pageInfo") or {}
        pages_scanned += 1

    (pr_obj.setdefault("reviews", {}))["pagesScanned"] = pages_scanned
    return result, data


def graphql_error(stage: str, result: CommandResult) -> dict[str, Any]:
    return {
        "stage": stage,
        "returncode": result.returncode,
        "stdout": compact(result.stdout),
        "stderr": compact(result.stderr),
    }


def pull_request(data: dict[str, Any] | None) -> dict[str, Any] | None:
    return (((data or {}).get("data") or {}).get("repository") or {}).get("pullRequest")


def reviewer_login(reviewer: dict[str, Any] | None) -> str:
    if not reviewer:
        return ""
    if reviewer.get("__typename") == "Team":
        org = ((reviewer.get("organization") or {}).get("login") or "").lower()
        slug = (reviewer.get("slug") or "").lower()
        return f"{org}/{slug}" if org and slug else slug
    return str(reviewer.get("login") or "").lower()


def actor_login(item: dict[str, Any]) -> str:
    author = item.get("author") or {}
    return str(author.get("login") or "").lower() if isinstance(author, dict) else ""


def is_copilot_login(login: str) -> bool:
    """Return true only for the known Copilot code-review bot identity."""
    return login.lower() in COPILOT_BOT_LOGINS


def is_copilot_error_review(review: dict[str, Any]) -> bool:
    body = str(review.get("body") or "").lower()
    return any(pattern in body for pattern in COPILOT_ERROR_PATTERNS)


def review_metadata(review: dict[str, Any]) -> dict[str, Any]:
    commit = review.get("commit") or {}
    body = str(review.get("body") or "")
    return {
        "author": actor_login(review),
        "state": review.get("state"),
        "url": review.get("url"),
        "submittedAt": review.get("submittedAt"),
        "commit": {"oid": commit.get("oid")},
        "body_length": len(body),
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest() if body else None,
    }


def review_metadata_list(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [review_metadata(review) for review in reviews]


def copilot_activity(data: dict[str, Any] | None) -> dict[str, Any]:
    pr = pull_request(data)
    if not pr:
        return {
            "visible": False,
            "reviews": [],
            "error_reviews": [],
            "review_requests": [],
            "reviewsPagesScanned": 0,
            "headRefOid": None,
            "headRefName": None,
            "headRepository": None,
            "baseRepository": None,
            "isDraft": None,
        }
    requests = []
    for node in ((pr.get("reviewRequests") or {}).get("nodes") or []):
        reviewer = (node or {}).get("requestedReviewer") or {}
        login = reviewer_login(reviewer)
        if is_copilot_login(login):
            requests.append({"login": login, "typename": reviewer.get("__typename")})
    head_sha = pr.get("headRefOid")
    reviews = []
    error_reviews = []
    for review in ((pr.get("reviews") or {}).get("nodes") or []):
        review_commit = ((review or {}).get("commit") or {}).get("oid")
        if is_copilot_login(actor_login(review)) and review_commit == head_sha:
            reviews.append(review)
            if is_copilot_error_review(review):
                error_reviews.append(review)
    return {
        "visible": bool(requests or reviews),
        "reviews": reviews,
        "error_reviews": error_reviews,
        "review_requests": requests,
        "reviewsPagesScanned": ((pr.get("reviews") or {}).get("pagesScanned") or 1),
        "headRefOid": head_sha,
        "headRefName": pr.get("headRefName"),
        "headRepository": ((pr.get("headRepository") or {}).get("nameWithOwner")),
        "baseRepository": ((pr.get("baseRepository") or {}).get("nameWithOwner")),
        "isDraft": pr.get("isDraft"),
    }


def repo_name_matches(actual: Any, expected: str) -> bool:
    return str(actual or "").casefold() == expected.casefold()


def validate_target_pr(
    activity: dict[str, Any],
    repo: str,
    *,
    allow_non_agent_branch: bool,
    allow_draft: bool,
    allow_fork: bool,
) -> list[str]:
    errors: list[str] = []
    if not repo_name_matches(activity.get("baseRepository"), repo):
        errors.append("PR base repository must match the requested repository.")
    if not allow_fork and not repo_name_matches(activity.get("headRepository"), repo):
        errors.append("PR head repository must match the requested repository for automated Copilot review.")
    if not allow_non_agent_branch and not str(activity.get("headRefName") or "").startswith("agent/"):
        errors.append("PR head branch must start with agent/.")
    if not allow_draft and activity.get("isDraft"):
        errors.append("Draft PRs are not eligible for automated Copilot review.")
    return errors


def normalize_actor(value: str) -> str:
    return value.strip().lstrip("@").casefold()


def split_allowlist(value: str) -> set[str]:
    return {normalize_actor(item) for item in value.split(",") if normalize_actor(item)}


def validate_override_actor(actor: str, allowed: str, override_used: bool) -> list[str]:
    if not override_used:
        return []
    allowed_set = split_allowlist(allowed)
    if not actor.strip():
        return ["Override requested but GITHUB_ACTOR_NAME/github.actor was not provided."]
    if normalize_actor(actor) not in allowed_set:
        return [f"Override actor {actor!r} is not in the Release Manager allowlist."]
    return []


def post_failure_comment(repo: str, pr: int, gh_version: str, edit_result: CommandResult, remediations: list[str], env: dict[str, str]) -> CommandResult:
    body = textwrap.dedent(
        f"""
        Copilot review request failed.

        ```text
        gh version:
        {compact(gh_version, 1200)}
        ```

        Command attempted:

        ```bash
        {shlex.join(edit_result.args)}
        ```

        Return code: `{edit_result.returncode}`

        Detailed stdout/stderr are intentionally not posted to PR comments.
        Check the workflow step summary or uploaded JSON evidence artifact.

        Likely remediation:
        {chr(10).join(f'- {hint}' for hint in remediations)}
        """
    ).strip()
    return run(["gh", "pr", "comment", str(pr), "--repo", repo, "--body", body], env=env)


def parse_min_version(value: str) -> tuple[int, int, int]:
    try:
        parts = tuple(int(part) for part in value.split("."))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("version must be MAJOR.MINOR.PATCH with numeric segments") from exc
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("version must be MAJOR.MINOR.PATCH")
    return parts  # type: ignore[return-value]


def main() -> int:
    parser = argparse.ArgumentParser(description="Request GitHub Copilot code review through gh CLI and verify GraphQL reviewRequests.")
    parser.add_argument("--repo", required=True, help="OWNER/REPO")
    parser.add_argument("--pr", required=True, type=int, help="Pull request number")
    parser.add_argument("--poll-seconds", type=int, default=0, help="Poll GraphQL reviewRequests/reviews for this many seconds")
    parser.add_argument("--poll-interval", type=int, default=5)
    parser.add_argument("--post-failure-comment", action="store_true")
    parser.add_argument("--fail-if-no-visible-review", action="store_true")
    parser.add_argument("--fail-on-copilot-error", action="store_true", help="Fail when current-head Copilot review reports an error and no new request is pending")
    parser.add_argument("--allow-non-agent-branch", action="store_true", help="Release Manager override: allow requesting Copilot on same-repo branches outside agent/*")
    parser.add_argument("--allow-draft", action="store_true", help="Release Manager override: allow requesting Copilot on draft PRs")
    parser.add_argument("--allow-fork", action="store_true", help="Release Manager override: allow requesting Copilot on fork PRs")
    parser.add_argument("--release-manager-actors", default=os.environ.get("RELEASE_MANAGER_ACTORS", ""), help="Comma-separated GitHub actors allowed to use override flags")
    parser.add_argument("--github-actor", default=os.environ.get("GITHUB_ACTOR_NAME") or os.environ.get("GITHUB_ACTOR") or "", help="GitHub actor invoking this helper; required when override flags are used")
    parser.add_argument("--min-gh-version", default=version_text(MIN_GH_VERSION), type=parse_min_version)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    args.repo = normalize_repo_full_name(args.repo)
    override_used = bool(args.allow_non_agent_branch or args.allow_draft or args.allow_fork)

    if not valid_repo_full_name(args.repo):
        print(json.dumps({
            "schema": "COPILOT-REVIEW-REQUEST:v1",
            "repo": args.repo,
            "pr": args.pr,
            "request_method": "gh pr edit --add-reviewer @copilot",
            "status": "invalid_repo",
            "error": "`--repo` must be in OWNER/REPO form with exactly one slash and valid owner/name characters.",
            "remediation": ["Pass a repository full name such as `jiseongnoh/aak-autonomy-demo`; surrounding whitespace is stripped, but whitespace inside owner/name is invalid."],
        }, indent=2))
        return 6

    env = os.environ.copy()
    env.setdefault("GH_PROMPT_DISABLED", "1")
    version_result = run(["gh", "--version"], env=env)
    gh_version = version_result.stdout or version_result.stderr
    actual_version = parse_gh_version(gh_version)
    version_ok = version_at_least(actual_version, args.min_gh_version)
    command = ["gh", "pr", "edit", str(args.pr), "--repo", args.repo, "--add-reviewer", COPILOT_REVIEWER]
    target_validated = False

    override_errors = validate_override_actor(args.github_actor, args.release_manager_actors, override_used)
    if override_errors:
        print(json.dumps({
            "schema": "COPILOT-REVIEW-REQUEST:v1",
            "repo": args.repo,
            "pr": args.pr,
            "request_method": "gh pr edit --add-reviewer @copilot",
            "gh_version": gh_version.strip(),
            "gh_returncode": version_result.returncode,
            "minimum_gh_version": version_text(args.min_gh_version),
            "status": "override_actor_validation_failed",
            "override": {
                "used": override_used,
                "allow_non_agent_branch": args.allow_non_agent_branch,
                "allow_draft": args.allow_draft,
                "allow_fork": args.allow_fork,
                "github_actor": args.github_actor,
                "release_manager_actors_configured": bool(split_allowlist(args.release_manager_actors)),
            },
            "errors": override_errors,
            "remediation": [
                "Only the Human Release Manager may use override flags.",
                "Set RELEASE_MANAGER_ACTORS/GITHUB_ACTOR_NAME in the workflow or remove the override flag.",
            ],
        }, indent=2))
        return 7

    if args.dry_run:
        print(json.dumps({
            "schema": "COPILOT-REVIEW-REQUEST:v1",
            "repo": args.repo,
            "pr": args.pr,
            "request_method": "gh pr edit --add-reviewer @copilot",
            "gh_version": gh_version.strip(),
            "gh_returncode": version_result.returncode,
            "minimum_gh_version": version_text(args.min_gh_version),
            "gh_version_ok": version_ok,
            "status": "dry_run",
            "command": command,
            "override": {
                "used": override_used,
                "allow_non_agent_branch": args.allow_non_agent_branch,
                "allow_draft": args.allow_draft,
                "allow_fork": args.allow_fork,
                "github_actor": args.github_actor,
            },
        }, indent=2))
        return 0

    if not version_ok:
        gh_unavailable = version_result.returncode == 127 or actual_version is None
        status = "gh_unavailable" if gh_unavailable else "preflight_failed"
        error = (
            "`gh` was not found or did not return a parseable version."
            if gh_unavailable
            else f"gh version must be >= {version_text(args.min_gh_version)}"
        )
        remediation = (
            ["Install GitHub CLI or ensure `gh` is executable on PATH."]
            if gh_unavailable
            else [f"Upgrade GitHub CLI to >= {version_text(args.min_gh_version)}."]
        )
        print(json.dumps({
            "schema": "COPILOT-REVIEW-REQUEST:v1",
            "repo": args.repo,
            "pr": args.pr,
            "request_method": "gh pr edit --add-reviewer @copilot",
            "gh_version": gh_version.strip(),
            "gh_returncode": version_result.returncode,
            "minimum_gh_version": version_text(args.min_gh_version),
            "status": status,
            "error": error,
            "remediation": remediation,
        }, indent=2))
        return 3

    graphql_errors: list[dict[str, Any]] = []
    before_result, before_data = gh_api_graphql(args.repo, args.pr, env)
    if before_result.returncode != 0 or before_data is None:
        graphql_errors.append(graphql_error("before", before_result))
    before_activity = copilot_activity(before_data)

    if graphql_errors:
        output = {
            "schema": "COPILOT-REVIEW-REQUEST:v1",
            "repo": args.repo,
            "pr": args.pr,
            "request_method": "gh pr edit --add-reviewer @copilot",
            "gh_version": gh_version.strip(),
            "minimum_gh_version": version_text(args.min_gh_version),
            "gh_version_ok": version_ok,
            "command": command,
            "returncode": None,
            "status": "graphql_error_before_request",
            "graphql_error_count": len(graphql_errors),
            "graphql_errors": graphql_errors,
            "remediation": [
                "Retry after checking GitHub API availability, token auth, and rate-limit state.",
                "Do not run the write-token Copilot request until the target PR can be classified.",
            ],
        }
        print(json.dumps(output, indent=2))
        return 5

    target_errors = validate_target_pr(
        before_activity,
        args.repo,
        allow_non_agent_branch=args.allow_non_agent_branch,
        allow_draft=args.allow_draft,
        allow_fork=args.allow_fork,
    )
    if target_errors:
        output = {
            "schema": "COPILOT-REVIEW-REQUEST:v1",
            "repo": args.repo,
            "pr": args.pr,
            "request_method": "gh pr edit --add-reviewer @copilot",
            "gh_version": gh_version.strip(),
            "minimum_gh_version": version_text(args.min_gh_version),
            "gh_version_ok": version_ok,
            "command": command,
            "returncode": None,
            "status": "target_validation_failed",
            "target": {
                "headRefOid": before_activity.get("headRefOid"),
                "headRefName": before_activity.get("headRefName"),
                "headRepository": before_activity.get("headRepository"),
                "baseRepository": before_activity.get("baseRepository"),
                "isDraft": before_activity.get("isDraft"),
            },
            "errors": target_errors,
            "remediation": [
                "Use same-repository PRs on agent/* branches for automated Copilot review requests.",
                "A Human Release Manager may rerun with the narrowest override flag, e.g. --allow-non-agent-branch, only for a scoped manual override.",
            ],
        }
        print(json.dumps(output, indent=2))
        return 6

    target_validated = True

    already_visible = bool(before_activity.get("review_requests") or before_activity.get("reviews"))
    needs_error_retry = bool(before_activity.get("error_reviews") and not before_activity.get("review_requests"))
    if already_visible and not needs_error_retry:
        edit_result = CommandResult(
            args=command,
            returncode=0,
            stdout="Copilot activity already visible; skipped duplicate review request.",
            stderr="",
        )
        status = "review_request_visible" if before_activity.get("review_requests") else "review_observed"
    else:
        edit_result = run(command, env=env)
        status = "request_failed" if edit_result.returncode != 0 else "requested_pending_visibility"
    remediations = likely_remediation(edit_result.stderr, edit_result.stdout, gh_version, args.min_gh_version)

    activity = before_activity
    deadline = time.time() + max(args.poll_seconds, 0)
    while edit_result.returncode == 0 and not (already_visible and not needs_error_retry):
        graphql_result, data = gh_api_graphql(args.repo, args.pr, env)
        if graphql_result.returncode != 0 or data is None:
            graphql_errors.append(graphql_error("poll", graphql_result))
            status = "graphql_error"
            break
        activity = copilot_activity(data)
        if activity.get("error_reviews") and activity.get("review_requests"):
            status = "review_request_visible_after_error"
            break
        if activity.get("error_reviews"):
            status = "review_error"
            break
        if activity.get("review_requests"):
            status = "review_request_visible"
            break
        if activity.get("reviews"):
            status = "review_observed"
            break
        if time.time() >= deadline:
            break
        time.sleep(max(args.poll_interval, 1))

    graphql_failure = edit_result.returncode == 0 and status == "graphql_error"
    no_visible_failure = (
        edit_result.returncode == 0
        and args.fail_if_no_visible_review
        and not activity.get("visible")
        and not graphql_failure
    )
    copilot_error_failure = (
        edit_result.returncode == 0
        and args.fail_on_copilot_error
        and status == "review_error"
    )
    comment_result: CommandResult | None = None
    failure_remediations: list[str] = []
    if edit_result.returncode != 0:
        failure_remediations = remediations
        if args.post_failure_comment and target_validated:
            comment_result = post_failure_comment(args.repo, args.pr, gh_version, edit_result, failure_remediations, env)
    elif graphql_failure:
        failure_remediations = [
            "Retry after checking GitHub API availability, token auth, and rate-limit state.",
            "Do not treat this as a missing Copilot review request; GraphQL validation itself failed.",
            "Keep `copilot-review-requested` blocked until exact Copilot reviewer evidence is visible for the current head SHA.",
        ]
        if args.post_failure_comment and target_validated:
            last_error = graphql_errors[-1] if graphql_errors else {}
            graphql_result = CommandResult(
                args=["gh", "api", "graphql"],
                returncode=5,
                stdout=str(last_error.get("stdout") or ""),
                stderr=str(last_error.get("stderr") or "GraphQL validation failed without a structured error."),
            )
            comment_result = post_failure_comment(
                args.repo,
                args.pr,
                gh_version,
                graphql_result,
                failure_remediations,
                env,
            )
    elif no_visible_failure:
        validation_result = CommandResult(
            args=command,
            returncode=2,
            stdout=edit_result.stdout,
            stderr=(
                "gh command succeeded, but GraphQL validation did not find "
                "the exact expected Copilot bot in reviewRequests or reviews "
                "inside the poll window."
            ),
        )
        failure_remediations = [
            "Confirm repository/organization Copilot code review is enabled.",
            "Confirm `gh pr edit --add-reviewer @copilot` is supported by the installed gh version.",
            "Inspect the PR review sidebar and GraphQL reviewRequests for delayed visibility.",
            "If Copilot already reviewed the current head, record `copilot-review-considered` instead of retrying indefinitely.",
            f"Expected exact Bot login: {', '.join(sorted(COPILOT_BOT_LOGINS))}.",
        ]
        if args.post_failure_comment and target_validated:
            comment_result = post_failure_comment(
                args.repo,
                args.pr,
                gh_version,
                validation_result,
                failure_remediations,
                env,
            )
    elif copilot_error_failure:
        error_result = CommandResult(
            args=command,
            returncode=4,
            stdout=edit_result.stdout,
            stderr=(
                "Copilot produced a current-head error review and no pending "
                "Copilot review request is visible."
            ),
        )
        failure_remediations = [
            "Re-request Copilot review once; if it fails again, require a Human Release Manager waiver for this PR/head SHA.",
            "Do not mark `copilot-review-considered` complete from a Copilot error review.",
            "Keep the PR blocked or human-gated until Copilot comments are reviewed, explicitly waived, or the policy is changed.",
        ]
        if args.post_failure_comment and target_validated:
            comment_result = post_failure_comment(
                args.repo,
                args.pr,
                gh_version,
                error_result,
                failure_remediations,
                env,
            )

    output = {
        "schema": "COPILOT-REVIEW-REQUEST:v1",
        "repo": args.repo,
        "pr": args.pr,
        "request_method": "gh pr edit --add-reviewer @copilot",
        "gh_version": gh_version.strip(),
        "minimum_gh_version": version_text(args.min_gh_version),
        "gh_version_ok": version_ok,
        "command": command,
        "returncode": edit_result.returncode,
        "stdout": compact(edit_result.stdout),
        "stderr": compact(edit_result.stderr),
        "status": status,
        "before": {
            "headRefOid": before_activity.get("headRefOid"),
            "headRefName": before_activity.get("headRefName"),
            "headRepository": before_activity.get("headRepository"),
            "baseRepository": before_activity.get("baseRepository"),
            "isDraft": before_activity.get("isDraft"),
            "copilot_review_requests_count": len(before_activity.get("review_requests", [])),
            "copilot_reviews_count": len(before_activity.get("reviews", [])),
        },
        "target_validation": {
            "allow_non_agent_branch": args.allow_non_agent_branch,
            "allow_draft": args.allow_draft,
            "allow_fork": args.allow_fork,
            "github_actor": args.github_actor,
            "override_actor_allowed": not override_errors,
            "errors": target_errors,
        },
        "copilot_activity_visible": activity.get("visible", False),
        "copilot_reviews_count": len(activity.get("reviews", [])),
        "copilot_reviews": review_metadata_list(activity.get("reviews", [])),
        "copilot_error_reviews_count": len(activity.get("error_reviews", [])),
        "copilot_error_reviews": review_metadata_list(activity.get("error_reviews", [])),
        "reviews_pages_scanned": activity.get("reviewsPagesScanned", 0),
        "copilot_review_requests_count": len(activity.get("review_requests", [])),
        "copilot_review_requests": activity.get("review_requests", []),
        "graphql_error_count": len(graphql_errors),
        "graphql_errors": graphql_errors,
        "target_head_sha": activity.get("headRefOid"),
        "expected_copilot_bot_logins": sorted(COPILOT_BOT_LOGINS),
        "remediation": failure_remediations,
        "failure_comment_posted": None if comment_result is None else comment_result.returncode == 0,
    }
    print(json.dumps(output, indent=2))

    if edit_result.returncode != 0:
        return edit_result.returncode
    if graphql_failure:
        return 5
    if args.fail_if_no_visible_review and not activity.get("visible"):
        return 2
    if copilot_error_failure:
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
