#!/usr/bin/env python3
"""Compute minimal human-agent collaboration metrics from COLLAB-EVENT:v1 JSONL."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

SCHEMA = "COLLAB-EVENT:v1"
TERMINAL_ACCEPTED = "human_accepted"
TERMINAL_CANCELLED = "task_cancelled"
SUPPORTED_TYPES = {
    "task_created",
    "agent_run_started",
    "agent_run_finished",
    "human_review_started",
    TERMINAL_ACCEPTED,
    TERMINAL_CANCELLED,
    "human_intervention",
    "changes_requested",
    "agent_reassigned",
    "defect_reported",
    "reverted",
    "cost_recorded",
}


def parse_time(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def seconds_between(start: dt.datetime | None, end: dt.datetime | None) -> int | None:
    if start is None or end is None:
        return None
    delta = int((end - start).total_seconds())
    return delta if delta >= 0 else None


def event_time(event: dict[str, Any]) -> dt.datetime | None:
    value = event.get("_occurred")
    return value if isinstance(value, dt.datetime) else parse_time(event.get("occurred_at"))


def derive_mode(mode: Any) -> str:
    if not isinstance(mode, dict):
        return "unknown"
    initiation = mode.get("initiation", "unknown")
    execution = mode.get("execution", "unknown")
    final = mode.get("final_authority", "unknown")
    if final == "automated":
        return "auto_final_authority"
    if final != "human":
        return "unknown"
    if initiation == "human_triggered" and execution == "suggestion_only":
        return "manual"
    if initiation in {"human_triggered", "human_ack"} and execution in {"bounded_write", "bounded_autonomous"}:
        return "semi_automatic"
    if initiation == "policy_preauthorized" and execution == "bounded_autonomous":
        return "fully_automatic_execution"
    return "unknown"


def validate_event(value: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(value, dict):
        return None, "not-object"
    if value.get("schema") != SCHEMA:
        return None, "bad-schema"
    event_id = value.get("event_id")
    task_id = value.get("task_id")
    event_type = value.get("event_type")
    if not isinstance(event_id, str) or not event_id.strip():
        return None, "missing-event-id"
    if not isinstance(task_id, str) or not task_id.strip():
        return None, "missing-task-id"
    if event_type not in SUPPORTED_TYPES:
        return None, "bad-event-type"
    if parse_time(value.get("occurred_at")) is None:
        return None, "bad-occurred-at"
    actor = value.get("actor")
    if not isinstance(actor, dict) or not isinstance(actor.get("type"), str):
        return None, "bad-actor"
    source = value.get("source")
    if not isinstance(source, dict) or not all(isinstance(source.get(k), str) for k in ("system", "record_id", "integrity")):
        return None, "bad-source"
    if value.get("authority") != "observation_only":
        return None, "bad-authority"
    return value, None


def load_events(path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    valid: list[dict[str, Any]] = []
    errors: dict[str, int] = {}
    seen: set[str] = set()
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            errors["invalid-json"] = errors.get("invalid-json", 0) + 1
            continue
        event, reason = validate_event(value)
        if reason:
            errors[reason] = errors.get(reason, 0) + 1
            continue
        assert event is not None
        event_id = event["event_id"]
        if event_id in seen:
            errors["duplicate-event-id"] = errors.get("duplicate-event-id", 0) + 1
            continue
        seen.add(event_id)
        event["_line_no"] = line_no
        event["_occurred"] = parse_time(event["occurred_at"])
        valid.append(event)
    valid.sort(key=lambda e: (e["_occurred"], e["_line_no"]))
    return valid, errors


def compute_metrics(events: list[dict[str, Any]], errors: dict[str, int] | None = None) -> dict[str, Any]:
    errors = dict(errors or {})
    tasks: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        tasks.setdefault(event["task_id"], []).append(event)

    completed_tasks = []
    cycle_seconds: list[int] = []
    human_wait_seconds: list[int] = []
    agent_active_by_agent: dict[str, int] = {}
    agent_completed_tasks: dict[str, int] = {}
    mode_counts: dict[str, int] = {}
    unknown_tasks = 0

    for task_id, task_events in tasks.items():
        accepted = next((e for e in task_events if e["event_type"] == TERMINAL_ACCEPTED), None)
        cancelled = any(e["event_type"] == TERMINAL_CANCELLED for e in task_events)
        if accepted is None or cancelled:
            continue
        completed_tasks.append(task_id)
        created = next((e for e in task_events if e["event_type"] == "task_created"), None)
        cycle = seconds_between(event_time(created) if created else None, event_time(accepted))
        if cycle is None:
            unknown_tasks += 1
        else:
            cycle_seconds.append(cycle)

        mode_counts[derive_mode(accepted.get("mode"))] = mode_counts.get(derive_mode(accepted.get("mode")), 0) + 1

        starts: dict[str, dict[str, Any]] = {}
        last_finish: dt.datetime | None = None
        agents_on_task: set[str] = set()
        for event in task_events:
            if event["event_type"] == "agent_run_started" and isinstance(event.get("correlation_id"), str):
                starts[event["correlation_id"]] = event
            elif event["event_type"] == "agent_run_finished" and isinstance(event.get("correlation_id"), str):
                start = starts.get(event["correlation_id"])
                start_agent_id = start.get("actor", {}).get("agent_id") if start else None
                finish_agent_id = event.get("actor", {}).get("agent_id")
                span = seconds_between(event_time(start) if start else None, event_time(event))
                if span is None:
                    errors["unmatched-or-invalid-agent-span"] = errors.get("unmatched-or-invalid-agent-span", 0) + 1
                elif (
                    isinstance(start_agent_id, str)
                    and isinstance(finish_agent_id, str)
                    and start_agent_id == finish_agent_id
                    and start.get("source", {}).get("integrity") == "verified"
                    and event.get("source", {}).get("integrity") == "verified"
                ):
                    agent_active_by_agent[finish_agent_id] = agent_active_by_agent.get(finish_agent_id, 0) + span
                    agents_on_task.add(finish_agent_id)
                    finish_time = event_time(event)
                    last_finish = finish_time if last_finish is None else max(last_finish, finish_time)
                else:
                    errors["unknown-agent-attribution"] = errors.get("unknown-agent-attribution", 0) + 1
            elif event["event_type"] == "human_review_started":
                wait = seconds_between(last_finish, event_time(event))
                if wait is not None:
                    human_wait_seconds.append(wait)
        for agent in agents_on_task:
            agent_completed_tasks[agent] = agent_completed_tasks.get(agent, 0) + 1

    return {
        "schema": "COLLAB-METRICS:v1",
        "tasks_observed": len(tasks),
        "completed_tasks": len(completed_tasks),
        "completed_task_ids": sorted(completed_tasks),
        "cycle_seconds": summarize(cycle_seconds),
        "agent_active_seconds_by_agent": dict(sorted(agent_active_by_agent.items())),
        "agent_completed_tasks": dict(sorted(agent_completed_tasks.items())),
        "human_wait_seconds": summarize(human_wait_seconds),
        "mode_counts": dict(sorted(mode_counts.items())),
        "unknown_counts": {**dict(sorted(errors.items())), "tasks_with_unknown_duration": unknown_tasks},
    }


def summarize(values: list[int]) -> dict[str, int | None]:
    if not values:
        return {"count": 0, "min": None, "max": None, "sum": 0}
    return {"count": len(values), "min": min(values), "max": max(values), "sum": sum(values)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("events_jsonl", type=Path)
    parser.add_argument("--output", type=Path, help="Write metrics JSON to this file instead of stdout.")
    args = parser.parse_args(argv)

    events, errors = load_events(args.events_jsonl)
    metrics = compute_metrics(events, errors)
    text = json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
