#!/usr/bin/env python3
"""Read-only PR monitor for manual-local-v1 agent workflows.

Polls `gh pr list` / `gh pr view` on a bounded interval and records observation
signals to local JSONL files. It is intentionally not a runner:
- no merge, approve, ACK, claim, enter, push, branch mutation, or workflow dispatch;
- optional GitHub issue comments are disabled unless explicitly requested;
- review workflow status is observed, never triggered.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
from typing import Any

DEFAULT_REPO = "jiseongnoh/aak-autonomy-demo"
DEFAULT_STATE_DIR = ".omc/state/read-only-pr-monitor"
DEFAULT_INTERVAL_SECONDS = 120
FORBIDDEN_AUTHORITY = [
    "auto_merge",
    "auto_ack",
    "auto_enter",
    "approve",
    "merge",
    "deploy",
    "workflow_dispatch",
    "branch_mutation",
    "push",
    "final_acceptance",
]
REVIEW_NAME_NEEDLES = ("review", "copilot", "claude", "advisory")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: pathlib.Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def write_json_atomic(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


def sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def gh_json(gh_bin: str, args: list[str], timeout_seconds: int) -> Any:
    completed = subprocess.run(
        [gh_bin, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or f"gh exited {completed.returncode}").strip()[:1200])
    stdout = completed.stdout.strip()
    return json.loads(stdout) if stdout else None


def is_review_check(check: dict[str, Any]) -> bool:
    name = str(check.get("name") or check.get("workflowName") or "").lower()
    return any(needle in name for needle in REVIEW_NAME_NEEDLES)


def compact_check(check: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": check.get("name") or check.get("workflowName"),
        "status": check.get("status"),
        "conclusion": check.get("conclusion"),
    }


def compact_review(review: dict[str, Any]) -> dict[str, Any]:
    author = review.get("author") or {}
    if isinstance(author, dict):
        author_login = author.get("login")
    else:
        author_login = str(author) if author else None
    return {
        "author": author_login,
        "state": review.get("state"),
        "submittedAt": review.get("submittedAt"),
    }


def collect_prs(repo: str, gh_bin: str, timeout_seconds: int, limit: int) -> list[dict[str, Any]]:
    base = gh_json(
        gh_bin,
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            str(limit),
            "--json",
            "number,title,headRefName,headRefOid,isDraft,updatedAt,createdAt,url,author",
        ],
        timeout_seconds,
    )
    if not isinstance(base, list):
        return []

    collected: list[dict[str, Any]] = []
    for pr in base:
        number = pr.get("number")
        try:
            detail = gh_json(
                gh_bin,
                [
                    "pr",
                    "view",
                    str(number),
                    "--repo",
                    repo,
                    "--json",
                    "number,reviewDecision,mergeStateStatus,statusCheckRollup,reviews,comments",
                ],
                timeout_seconds,
            )
            if isinstance(detail, dict):
                pr.update(detail)
        except Exception as exc:
            pr["monitor_error"] = str(exc)[:500]
        checks = pr.get("statusCheckRollup") or []
        reviews = pr.get("reviews") or []
        review_checks = [compact_check(c) for c in checks if isinstance(c, dict) and is_review_check(c)]
        review_items = [compact_review(r) for r in reviews[-12:] if isinstance(r, dict)]
        pr["review_workflow_summary"] = {
            "reviewDecision": pr.get("reviewDecision"),
            "mergeStateStatus": pr.get("mergeStateStatus"),
            "reviewChecks": review_checks,
            "recentReviews": review_items,
        }
        pr["review_workflow_digest"] = sha256_json(pr["review_workflow_summary"])
        collected.append(pr)
    return collected


def pr_key(pr: dict[str, Any]) -> str:
    return str(pr.get("number"))


def detect_events(prs: list[dict[str, Any]], state: dict[str, Any], *, baseline: bool, emit_existing: bool) -> list[dict[str, Any]]:
    now = utc_now()
    seen = state.setdefault("seen_prs", {})
    events: list[dict[str, Any]] = []
    for pr in prs:
        key = pr_key(pr)
        head = str(pr.get("headRefOid") or "")
        digest = str(pr.get("review_workflow_digest") or "")
        previous = seen.get(key)
        payload = {
            "repo": state.get("repo"),
            "pr": pr.get("number"),
            "title": pr.get("title"),
            "url": pr.get("url"),
            "headRefName": pr.get("headRefName"),
            "headRefOid": head,
            "isDraft": pr.get("isDraft"),
            "review_workflow_summary": pr.get("review_workflow_summary"),
            "authority": "observation_only",
            "forbidden": FORBIDDEN_AUTHORITY,
        }
        if not previous:
            if not baseline or emit_existing:
                events.append({"ts": now, "type": "new_open_pr", **payload})
        else:
            if head and head != previous.get("headRefOid"):
                events.append({"ts": now, "type": "pr_head_changed", "previousHeadRefOid": previous.get("headRefOid"), **payload})
            elif digest and digest != previous.get("review_workflow_digest"):
                events.append({"ts": now, "type": "review_workflow_status_changed", **payload})
        seen[key] = {
            "headRefOid": head,
            "headRefName": pr.get("headRefName"),
            "title": pr.get("title"),
            "url": pr.get("url"),
            "isDraft": pr.get("isDraft"),
            "updatedAt": pr.get("updatedAt"),
            "review_workflow_digest": digest,
            "lastSeenAt": now,
        }
    return events


def notify(title: str, message: str) -> None:
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                'on run argv\n display notification (item 2 of argv) with title (item 1 of argv)\nend run',
                title,
                message[:240],
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
    except Exception:
        pass


def build_issue_comment(events: list[dict[str, Any]], repo: str) -> str:
    lines = [
        "READ-ONLY-PR-MONITOR:v1",
        f"repo: {repo}",
        f"observed_at: {utc_now()}",
        "authority: observation_only",
        "forbidden: auto-merge, auto-ACK, auto-enter, approval, final acceptance",
        "",
        "Detected PR signal(s):",
    ]
    for event in events:
        lines.append(f"- {event['type']}: PR #{event.get('pr')} `{str(event.get('headRefOid') or '')[:12]}` — {event.get('title')}")
    lines.extend([
        "",
        "This is monitoring evidence only. It does not authorize implementation, ACK, merge, release, or permission changes.",
    ])
    return "\n".join(lines) + "\n"


def maybe_record_or_post_issue_comment(args: argparse.Namespace, events: list[dict[str, Any]], state_dir: pathlib.Path) -> None:
    if not events or not args.issue_comment_target:
        return
    body = build_issue_comment(events, args.repo)
    comments_dir = state_dir / "prepared-comments"
    comments_dir.mkdir(parents=True, exist_ok=True)
    comment_path = comments_dir / f"{utc_now().replace(':', '').replace('-', '')}-pr-monitor.md"
    comment_path.write_text(body, encoding="utf-8")
    if not args.post_issue_comments:
        return
    subprocess.run(
        [args.gh_bin, "issue", "comment", str(args.issue_comment_target), "--repo", args.repo, "--body-file", str(comment_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=args.gh_timeout,
        check=True,
    )


def poll_once(args: argparse.Namespace, state_dir: pathlib.Path, state: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    events_file = state_dir / "events.jsonl"
    latest_file = state_dir / "latest.json"
    heartbeat_file = state_dir / "heartbeat.json"

    state["repo"] = args.repo
    state["cycles"] = int(state.get("cycles") or 0) + 1
    baseline = not bool(state.get("initialized"))
    heartbeat = {
        "schema_version": "read_only_pr_monitor.heartbeat.v1",
        "repo": args.repo,
        "cycle": state["cycles"],
        "at": utc_now(),
        "authority": "observation_only",
        "forbidden": FORBIDDEN_AUTHORITY,
    }
    write_json_atomic(heartbeat_file, heartbeat)

    prs = collect_prs(args.repo, args.gh_bin, args.gh_timeout, args.limit)
    snapshot = {
        "schema_version": "read_only_pr_monitor.latest.v1",
        "repo": args.repo,
        "observed_at": utc_now(),
        "open_pr_count": len(prs),
        "prs": prs,
        "authority": "observation_only",
    }
    write_json_atomic(latest_file, snapshot)

    events = detect_events(prs, state, baseline=baseline, emit_existing=args.emit_existing)
    state["initialized"] = True
    state["last_poll_at"] = utc_now()
    state["open_pr_count"] = len(prs)

    for event in events:
        append_jsonl(events_file, event)
        if args.notify:
            notify("Read-only PR monitor", f"{args.repo}: {event['type']} PR #{event.get('pr')}")
    maybe_record_or_post_issue_comment(args, events, state_dir)
    return state, events


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only GitHub PR monitor: local signals only, no auto merge/ACK/enter.")
    parser.add_argument("--repo", default=os.environ.get("REPO", DEFAULT_REPO), help="GitHub owner/repo to watch")
    parser.add_argument("--state-dir", default=os.environ.get("STATE_DIR", DEFAULT_STATE_DIR), help="Local state/log directory")
    parser.add_argument("--interval", type=positive_int, default=int(os.environ.get("POLL_SECONDS", str(DEFAULT_INTERVAL_SECONDS))), help="Polling interval seconds; recommended 60-180")
    parser.add_argument("--max-cycles", type=int, default=int(os.environ.get("MAX_CYCLES", "0")), help="Stop after N cycles; 0 means run until Ctrl-C")
    parser.add_argument("--once", action="store_true", help="Run one poll cycle and exit")
    parser.add_argument("--limit", type=positive_int, default=int(os.environ.get("PR_LIMIT", "50")), help="Max open PRs to inspect")
    parser.add_argument("--gh-bin", default=os.environ.get("GH_BIN", shutil.which("gh") or "gh"))
    parser.add_argument("--gh-timeout", type=positive_int, default=int(os.environ.get("GH_TIMEOUT", "30")), help="Timeout seconds per gh call")
    parser.add_argument("--notify", action="store_true", help="Send macOS notification for new signals")
    parser.add_argument("--emit-existing", action="store_true", help="Emit existing open PRs during first baseline cycle")
    parser.add_argument("--issue-comment-target", help="Issue number for optional monitor comments; writes prepared local comment by default")
    parser.add_argument("--post-issue-comments", action="store_true", help="Actually post issue comments; requires --issue-comment-target and is the only GitHub write path")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.post_issue_comments and not args.issue_comment_target:
        parser.error("--post-issue-comments requires --issue-comment-target")
    state_dir = pathlib.Path(args.state_dir).expanduser()
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "state.json"
    state = read_json(state_file, {"schema_version": "read_only_pr_monitor.state.v1", "seen_prs": {}})

    print(f"[read-only-pr-monitor] repo={args.repo} interval={args.interval}s state={state_dir}")
    print("[read-only-pr-monitor] observation-only; no auto merge/ACK/enter; Ctrl-C to stop")
    cycles = 0
    while True:
        cycles += 1
        try:
            state, events = poll_once(args, state_dir, state)
            write_json_atomic(state_file, state)
            print(f"[read-only-pr-monitor] cycle={state.get('cycles')} events={len(events)} open_prs={state.get('open_pr_count')} at={state.get('last_poll_at')}")
        except KeyboardInterrupt:
            print("[read-only-pr-monitor] stopped")
            return 130
        except Exception as exc:
            append_jsonl(state_dir / "errors.jsonl", {"ts": utc_now(), "type": "poll_error", "error": str(exc)[:1200]})
            print(f"[read-only-pr-monitor] warn: {exc}", file=sys.stderr)
        if args.once or (args.max_cycles and cycles >= args.max_cycles):
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
