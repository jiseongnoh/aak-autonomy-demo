#!/usr/bin/env python3
"""Validate and assess Evidence Bundle v1 artifacts.

This tool is deliberately small and deterministic. It reads one JSON evidence
bundle and optional JSON policy metadata; it never executes repository code,
never reads environment variables for policy, never performs network access,
and never treats its output as approval, merge, deploy, ACK, or final authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

BUNDLE_SCHEMA_VERSION = "EVIDENCE-BUNDLE:v1"
POLICY_SCHEMA_VERSION = "EVIDENCE-POLICY:v1"
ASSESSMENT_SCHEMA_VERSION = "EVIDENCE-ASSESSMENT:v1"
DEFAULT_POLICY_ID = "evidence-default-v1"
AUTHORITY_EFFECT = "NONE"

BUNDLE_STATUSES = {"SUPPORTED", "REFUTED", "INSUFFICIENT"}
ARTIFACT_STATUSES = {"OK", "VIOLATION", "MISSING", "ERROR"}
ASSUMPTION_STATES = {"SUPPORTED", "CONTRADICTED", "UNKNOWN"}
INVARIANT_CRITICALITIES = {"normal", "safety", "authority"}
RISK_SEVERITIES = {"low", "medium", "high", "critical"}
REASON_CODES = {
    "ok",
    "invalid_bundle",
    "invalid_reference",
    "head_sha_mismatch",
    "no_invariants",
    "invariant_missing_evidence",
    "artifact_violation",
    "artifact_missing",
    "artifact_error",
    "assumption_contradicted",
    "assumption_unknown",
    "high_residual_risk",
    "critical_residual_risk",
    "authority_human_decision_required",
    "policy_invalid",
}

DEFAULT_POLICY = {
    "schema_version": POLICY_SCHEMA_VERSION,
    "policy_id": DEFAULT_POLICY_ID,
    "authority_effect": AUTHORITY_EFFECT,
}
# Policy files identify the policy version used in output. They do not loosen
# core safety guards; hard fail-closed rules live in assess_bundle().

OBJECT_KEYS: dict[str, set[str]] = {
    "bundle": {
        "schema_version",
        "bundle_id",
        "created_at",
        "subject",
        "intent",
        "artifacts",
        "assumptions",
        "untested_regions",
        "residual_risks",
        "assessment",
    },
    "subject": {"repository_id", "base_sha", "head_sha", "issue", "pull_request"},
    "intent": {"goal", "invariants", "non_goals"},
    "invariant": {"id", "statement", "criticality"},
    "artifact": {
        "id",
        "kind",
        "producer",
        "subject_sha",
        "verifies",
        "does_not_verify",
        "status",
        "path",
        "sha256",
    },
    "assumption": {"id", "statement", "evidence_ids", "state"},
    "residual_risk": {"id", "severity", "statement"},
    "assessment": {"status", "policy_id", "reason_codes", "authority_effect"},
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def repo_root_from(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for path in (current, *current.parents):
        if (path / ".git").exists():
            return path
    return Path.cwd().resolve()


def default_schema_path(root: Path) -> Path:
    installed = root / "schemas" / "evidence-bundle.v1.schema.json"
    dev = root / "files" / "common" / "schemas" / "evidence-bundle.v1.schema.json"
    return installed if installed.exists() else dev


def default_policy_path(root: Path) -> Path:
    installed = root / "config" / "agent-kit" / "evidence-policy.v1.json"
    dev = root / "files" / "common" / "config" / "agent-kit" / "evidence-policy.v1.json"
    return installed if installed.exists() else dev


def load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc.msg}") from exc
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc


def load_policy(path: Path | None, root: Path) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    if path is None:
        candidate = default_policy_path(root)
        if not candidate.exists():
            warnings.append("default policy file not found; using built-in fail-closed policy metadata")
            return dict(DEFAULT_POLICY), warnings
        path = candidate
    value = load_json_file(path)
    if not isinstance(value, dict):
        raise ValueError("policy must be a JSON object")
    extra_keys = set(value) - {"schema_version", "policy_id", "authority_effect"}
    if extra_keys:
        raise ValueError(f"policy contains unknown fields: {', '.join(sorted(extra_keys))}")
    if value.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise ValueError("policy schema_version must be EVIDENCE-POLICY:v1")
    if not isinstance(value.get("policy_id"), str) or not value["policy_id"].strip():
        raise ValueError("policy_id must be a non-empty string")
    if value.get("authority_effect") != AUTHORITY_EFFECT:
        raise ValueError("policy authority_effect must be NONE")
    return value, warnings


def is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def require_keys(value: Any, *, required: set[str], allowed: set[str], where: str, errors: list[str]) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{where}: not-object")
        return False
    extras = set(value) - allowed
    missing = required - set(value)
    for key in sorted(extras):
        errors.append(f"{where}: unknown-field:{key}")
    for key in sorted(missing):
        errors.append(f"{where}: missing-field:{key}")
    return not extras and not missing


def check_string(value: Any, where: str, errors: list[str], *, nonempty: bool = True) -> None:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        errors.append(f"{where}: bad-string")


def check_string_array(value: Any, where: str, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append(f"{where}: not-array")
        return
    for index, item in enumerate(value):
        if not isinstance(item, str):
            errors.append(f"{where}[{index}]: not-string")


def check_unique(ids: list[str], where: str, errors: list[str]) -> None:
    seen: set[str] = set()
    for item in ids:
        if item in seen:
            errors.append(f"{where}: duplicate-id:{item}")
        seen.add(item)


def is_safe_rel_path(value: str) -> bool:
    if not value or "\x00" in value or value.startswith("/") or "\\" in value:
        return False
    rel = PurePosixPath(value)
    if rel.is_absolute():
        return False
    return all(part not in {"", ".", ".."} for part in rel.parts)


def validate_bundle(value: Any) -> list[str]:
    errors: list[str] = []
    if not require_keys(
        value,
        required={"schema_version", "bundle_id", "created_at", "subject", "intent", "artifacts", "assumptions", "untested_regions", "residual_risks"},
        allowed=OBJECT_KEYS["bundle"],
        where="bundle",
        errors=errors,
    ):
        if not isinstance(value, dict):
            return errors
    if value.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        errors.append("bundle.schema_version: must-be:EVIDENCE-BUNDLE:v1")
    check_string(value.get("bundle_id"), "bundle.bundle_id", errors)
    check_string(value.get("created_at"), "bundle.created_at", errors)

    subject = value.get("subject")
    if require_keys(subject, required={"repository_id", "base_sha", "head_sha"}, allowed=OBJECT_KEYS["subject"], where="subject", errors=errors):
        check_string(subject.get("repository_id"), "subject.repository_id", errors)
        check_string(subject.get("base_sha"), "subject.base_sha", errors)
        check_string(subject.get("head_sha"), "subject.head_sha", errors)
        for optional in ("issue", "pull_request"):
            if optional in subject and subject[optional] is not None and not isinstance(subject[optional], int):
                errors.append(f"subject.{optional}: must-be-integer-or-null")

    intent = value.get("intent")
    invariant_ids: list[str] = []
    if require_keys(intent, required={"goal", "invariants", "non_goals"}, allowed=OBJECT_KEYS["intent"], where="intent", errors=errors):
        check_string(intent.get("goal"), "intent.goal", errors, nonempty=False)
        check_string_array(intent.get("non_goals"), "intent.non_goals", errors)
        invariants = intent.get("invariants")
        if not isinstance(invariants, list):
            errors.append("intent.invariants: not-array")
        else:
            for index, invariant in enumerate(invariants):
                where = f"intent.invariants[{index}]"
                if require_keys(invariant, required={"id", "statement", "criticality"}, allowed=OBJECT_KEYS["invariant"], where=where, errors=errors):
                    check_string(invariant.get("id"), f"{where}.id", errors)
                    check_string(invariant.get("statement"), f"{where}.statement", errors, nonempty=False)
                    if invariant.get("criticality") not in INVARIANT_CRITICALITIES:
                        errors.append(f"{where}.criticality: bad-enum")
                    if is_nonempty_string(invariant.get("id")):
                        invariant_ids.append(invariant["id"])
            check_unique(invariant_ids, "intent.invariants", errors)
    invariant_id_set = set(invariant_ids)

    artifact_ids: list[str] = []
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list):
        errors.append("artifacts: not-array")
    else:
        for index, artifact in enumerate(artifacts):
            where = f"artifacts[{index}]"
            if require_keys(artifact, required=OBJECT_KEYS["artifact"], allowed=OBJECT_KEYS["artifact"], where=where, errors=errors):
                for key in ("id", "kind", "producer", "subject_sha", "path"):
                    check_string(artifact.get(key), f"{where}.{key}", errors)
                if is_nonempty_string(artifact.get("id")):
                    artifact_ids.append(artifact["id"])
                if artifact.get("status") not in ARTIFACT_STATUSES:
                    errors.append(f"{where}.status: bad-enum")
                if not isinstance(artifact.get("sha256"), str) or not SHA256_RE.match(artifact["sha256"]):
                    errors.append(f"{where}.sha256: bad-sha256")
                if not isinstance(artifact.get("path"), str) or not is_safe_rel_path(artifact["path"]):
                    errors.append(f"{where}.path: unsafe-path")
                for field in ("verifies", "does_not_verify"):
                    refs = artifact.get(field)
                    check_string_array(refs, f"{where}.{field}", errors)
                    if isinstance(refs, list):
                        for ref in refs:
                            if isinstance(ref, str) and ref not in invariant_id_set:
                                errors.append(f"{where}.{field}: unknown-invariant:{ref}")
        check_unique(artifact_ids, "artifacts", errors)
    artifact_id_set = set(artifact_ids)

    assumptions = value.get("assumptions")
    assumption_ids: list[str] = []
    if not isinstance(assumptions, list):
        errors.append("assumptions: not-array")
    else:
        for index, assumption in enumerate(assumptions):
            where = f"assumptions[{index}]"
            if require_keys(assumption, required=OBJECT_KEYS["assumption"], allowed=OBJECT_KEYS["assumption"], where=where, errors=errors):
                check_string(assumption.get("id"), f"{where}.id", errors)
                check_string(assumption.get("statement"), f"{where}.statement", errors, nonempty=False)
                if is_nonempty_string(assumption.get("id")):
                    assumption_ids.append(assumption["id"])
                if assumption.get("state") not in ASSUMPTION_STATES:
                    errors.append(f"{where}.state: bad-enum")
                refs = assumption.get("evidence_ids")
                check_string_array(refs, f"{where}.evidence_ids", errors)
                if isinstance(refs, list):
                    for ref in refs:
                        if isinstance(ref, str) and ref not in artifact_id_set:
                            errors.append(f"{where}.evidence_ids: unknown-artifact:{ref}")
        check_unique(assumption_ids, "assumptions", errors)

    check_string_array(value.get("untested_regions"), "untested_regions", errors)
    residual_risks = value.get("residual_risks")
    risk_ids: list[str] = []
    if not isinstance(residual_risks, list):
        errors.append("residual_risks: not-array")
    else:
        for index, risk in enumerate(residual_risks):
            where = f"residual_risks[{index}]"
            if require_keys(risk, required=OBJECT_KEYS["residual_risk"], allowed=OBJECT_KEYS["residual_risk"], where=where, errors=errors):
                check_string(risk.get("id"), f"{where}.id", errors)
                check_string(risk.get("statement"), f"{where}.statement", errors, nonempty=False)
                if is_nonempty_string(risk.get("id")):
                    risk_ids.append(risk["id"])
                if risk.get("severity") not in RISK_SEVERITIES:
                    errors.append(f"{where}.severity: bad-enum")
        check_unique(risk_ids, "residual_risks", errors)

    if "assessment" in value:
        assessment = value.get("assessment")
        if require_keys(assessment, required=OBJECT_KEYS["assessment"], allowed=OBJECT_KEYS["assessment"], where="assessment", errors=errors):
            if assessment.get("status") not in BUNDLE_STATUSES:
                errors.append("assessment.status: bad-enum")
            check_string(assessment.get("policy_id"), "assessment.policy_id", errors)
            if assessment.get("authority_effect") != AUTHORITY_EFFECT:
                errors.append("assessment.authority_effect: must-be:NONE")
            reason_codes = assessment.get("reason_codes")
            check_string_array(reason_codes, "assessment.reason_codes", errors)
            if isinstance(reason_codes, list):
                for code in reason_codes:
                    if isinstance(code, str) and code not in REASON_CODES:
                        errors.append(f"assessment.reason_codes: unknown-reason:{code}")
    return errors


def assess_bundle(bundle: dict[str, Any], policy: dict[str, Any] | None = None, *, warnings: list[str] | None = None) -> dict[str, Any]:
    policy = policy or DEFAULT_POLICY
    warnings = list(warnings or [])
    errors = validate_bundle(bundle)
    reason_codes: set[str] = set()
    invalid_references = [error for error in errors if "unknown-" in error or "duplicate-id" in error]
    if errors:
        reason_codes.add("invalid_bundle")
    if invalid_references:
        reason_codes.add("invalid_reference")

    status = "SUPPORTED"
    if errors:
        status = "INSUFFICIENT"

    subject = bundle.get("subject") if isinstance(bundle, dict) else None
    head_sha = subject.get("head_sha") if isinstance(subject, dict) else None
    artifacts = bundle.get("artifacts") if isinstance(bundle, dict) else []
    if isinstance(artifacts, list):
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            artifact_status = artifact.get("status")
            if head_sha and artifact.get("subject_sha") != head_sha:
                reason_codes.add("head_sha_mismatch")
                if status == "SUPPORTED":
                    status = "INSUFFICIENT"
            if artifact_status == "VIOLATION":
                reason_codes.add("artifact_violation")
                status = "REFUTED"
            elif artifact_status == "MISSING":
                reason_codes.add("artifact_missing")
                if status == "SUPPORTED":
                    status = "INSUFFICIENT"
            elif artifact_status == "ERROR":
                reason_codes.add("artifact_error")
                if status == "SUPPORTED":
                    status = "INSUFFICIENT"

    intent = bundle.get("intent") if isinstance(bundle, dict) else None
    invariants = intent.get("invariants") if isinstance(intent, dict) else []
    if not isinstance(invariants, list) or not invariants:
        reason_codes.add("no_invariants")
        if status == "SUPPORTED":
            status = "INSUFFICIENT"
    else:
        ok_verifies: dict[str, int] = {}
        if isinstance(artifacts, list):
            for artifact in artifacts:
                if not isinstance(artifact, dict) or artifact.get("status") != "OK":
                    continue
                for invariant_id in artifact.get("verifies", []):
                    if isinstance(invariant_id, str):
                        ok_verifies[invariant_id] = ok_verifies.get(invariant_id, 0) + 1
        for invariant in invariants:
            if not isinstance(invariant, dict):
                continue
            invariant_id = invariant.get("id")
            if invariant.get("criticality") == "authority":
                reason_codes.add("authority_human_decision_required")
                if status == "SUPPORTED":
                    status = "INSUFFICIENT"
            if isinstance(invariant_id, str) and ok_verifies.get(invariant_id, 0) < 1:
                reason_codes.add("invariant_missing_evidence")
                if status == "SUPPORTED":
                    status = "INSUFFICIENT"

    assumptions = bundle.get("assumptions") if isinstance(bundle, dict) else []
    if isinstance(assumptions, list):
        for assumption in assumptions:
            if not isinstance(assumption, dict):
                continue
            if assumption.get("state") == "CONTRADICTED":
                reason_codes.add("assumption_contradicted")
                status = "REFUTED"
            elif assumption.get("state") == "UNKNOWN":
                reason_codes.add("assumption_unknown")
                if status == "SUPPORTED":
                    status = "INSUFFICIENT"

    residual_risks = bundle.get("residual_risks") if isinstance(bundle, dict) else []
    if isinstance(residual_risks, list):
        for risk in residual_risks:
            if not isinstance(risk, dict):
                continue
            if risk.get("severity") == "high":
                reason_codes.add("high_residual_risk")
                if status == "SUPPORTED":
                    status = "INSUFFICIENT"
            elif risk.get("severity") == "critical":
                reason_codes.add("critical_residual_risk")
                if status == "SUPPORTED":
                    status = "INSUFFICIENT"

    if not reason_codes:
        reason_codes.add("ok")

    return {
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "bundle_id": bundle.get("bundle_id") if isinstance(bundle, dict) else None,
        "policy_id": policy.get("policy_id", DEFAULT_POLICY_ID),
        "status": status,
        "reason_codes": sorted(reason_codes),
        "authority_effect": AUTHORITY_EFFECT,
        "human_decision_required": "authority_human_decision_required" in reason_codes,
        "bundle_sha256": sha256_json(bundle),
        "warnings": warnings,
        "errors": errors,
    }


def write_json(value: Any, path: Path | None = None) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def validate_command(args: argparse.Namespace) -> int:
    root = args.root.resolve() if args.root else repo_root_from(args.bundle)
    schema_path = args.schema or default_schema_path(root)
    if args.schema and not schema_path.exists():
        write_json({"ok": False, "schema_version": ASSESSMENT_SCHEMA_VERSION, "error": f"missing file: {schema_path}", "authority_effect": AUTHORITY_EFFECT})
        return 1
    if schema_path.exists():
        try:
            schema = load_json_file(schema_path)
            if not isinstance(schema, dict) or schema.get("title") != "Evidence Bundle v1":
                raise ValueError("unexpected evidence schema file")
        except ValueError as exc:
            write_json({"ok": False, "schema_version": ASSESSMENT_SCHEMA_VERSION, "error": str(exc), "authority_effect": AUTHORITY_EFFECT})
            return 1
    try:
        bundle = load_json_file(args.bundle)
    except ValueError as exc:
        write_json({"ok": False, "schema_version": ASSESSMENT_SCHEMA_VERSION, "error": str(exc), "authority_effect": AUTHORITY_EFFECT})
        return 1
    errors = validate_bundle(bundle)
    result = {
        "ok": not errors,
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "bundle_id": bundle.get("bundle_id") if isinstance(bundle, dict) else None,
        "errors": errors,
        "authority_effect": AUTHORITY_EFFECT,
    }
    write_json(result, args.out)
    return 0 if not errors else 1


def error_assessment(*, reason: str, message: str) -> dict[str, Any]:
    error_bundle = {"bundle_id": None, "error": message}
    return {
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "bundle_id": None,
        "policy_id": DEFAULT_POLICY_ID,
        "status": "INSUFFICIENT",
        "reason_codes": [reason],
        "authority_effect": AUTHORITY_EFFECT,
        "human_decision_required": True,
        "bundle_sha256": sha256_json(error_bundle),
        "warnings": [],
        "errors": [message],
    }


def assess_command(args: argparse.Namespace) -> int:
    root = args.root.resolve() if args.root else repo_root_from(args.bundle)
    try:
        bundle = load_json_file(args.bundle)
    except ValueError as exc:
        write_json(error_assessment(reason="invalid_bundle", message=str(exc)), args.out)
        return 1
    try:
        policy, warnings = load_policy(args.policy, root)
    except ValueError as exc:
        write_json(error_assessment(reason="policy_invalid", message=str(exc)), args.out)
        return 1
    result = assess_bundle(bundle, policy, warnings=warnings)
    write_json(result, args.out)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="Validate an Evidence Bundle v1 JSON file.")
    validate.add_argument("--bundle", required=True, type=Path)
    validate.add_argument("--schema", type=Path, help="Optional schema metadata path; defaults to schemas/evidence-bundle.v1.schema.json under repo root.")
    validate.add_argument("--root", type=Path, help="Repository root for default schema discovery.")
    validate.add_argument("--out", type=Path, help="Write JSON result to this file instead of stdout.")

    assess = sub.add_parser("assess", help="Produce a deterministic advisory assessment for an Evidence Bundle v1 JSON file.")
    assess.add_argument("--bundle", required=True, type=Path)
    assess.add_argument("--policy", type=Path, help="Optional JSON policy path; defaults to config/agent-kit/evidence-policy.v1.json under repo root.")
    assess.add_argument("--root", type=Path, help="Repository root for default policy discovery.")
    assess.add_argument("--out", type=Path, help="Write JSON assessment to this file instead of stdout.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "validate":
        return validate_command(args)
    if args.command == "assess":
        return assess_command(args)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
