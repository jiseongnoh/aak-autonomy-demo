"""Exercise the Python and shell entrypoints embedded in the workflow itself."""

import contextlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "request-copilot-review.yml"
POST_STEP = "Post Claude fallback review comment"
GATE_STEP = "Gate Copilot review request evidence"
BUILD_STEP = "Build bounded Claude fallback prompt"
SHA = "a" * 40
BASE_SHA = "b" * 40
REPO = "jiseongnoh/aak-autonomy-demo"
PR = 17


def embedded_run(workflow, step_name):
    text = Path(workflow).read_text(encoding="utf-8")
    step = text.index(f"- name: {step_name}")
    start = text.index("        run: |\n", step) + len("        run: |\n")
    body = []
    for line in text[start:].splitlines():
        if line.startswith("          "):
            body.append(line[10:])
        elif not line.strip():
            body.append("")
        else:
            break
    return "\n".join(body)


def embedded_python(workflow, step_name):
    run = embedded_run(workflow, step_name)
    match = re.search(r"(?ms)^python3 - <<'PY'\n(.*?)^PY$", run)
    if not match:
        raise AssertionError(f"embedded Python block missing in {step_name}")
    return match.group(1)


def response(*, result="success", verdict="pass", head=SHA, truncated=False, malformed=False):
    findings = [] if verdict == "pass" else [{"severity": "medium", "title": "Finding", "description": "Advisory finding."}]
    report = {
        "schema": "CLAUDE-FALLBACK-REVIEW:v1", "result": result, "repo": REPO, "pr": PR,
        "target_head_sha": head, "fallback_for": "copilot-review-unavailable-or-error",
        "advisory_only": True, "findings": findings, "verdict": verdict,
        "reviewed_by": "claude-code-action", "diff_truncated": truncated,
    }
    encoded = "{bad json" if malformed else json.dumps(report)
    return f"Short review summary.\nCLAUDE-FALLBACK-REVIEW:v1\n```json\n{encoded}\n```"


def execute_post(*, text=None, raw_output=None, execution_exists=True, target=SHA,
                 current_heads=None, comment_fails=False, diff_truncated="false", terminal=None):
    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        output_path = temp / "outputs.txt"
        execution_path = temp / "execution.jsonl"
        if execution_exists:
            assistant = {"type": "assistant", "message": {"content": [{"type": "text", "text": text or response()}]}}
            terminal = terminal or {"type": "result", "subtype": "success", "is_error": False}
            execution_path.write_text(raw_output if raw_output is not None else
                                      json.dumps(assistant) + "\n" + json.dumps(terminal) + "\n", encoding="utf-8")
        env = {
            "REPO": REPO, "PR_NUMBER": str(PR), "EXECUTION_FILE": str(execution_path) if execution_exists else "",
            "RUN_URL": "https://github.com/example/run/1", "TARGET_HEAD_SHA": target,
            "GITHUB_OUTPUT": str(output_path), "DIFF_TRUNCATED": diff_truncated,
        }
        heads = list(current_heads or [SHA, SHA])
        seen = []

        def fake_head(*args, **kwargs):
            seen.append((args, kwargs))
            return json.dumps({"headRefOid": heads[min(len(seen) - 1, len(heads) - 1)]})

        run_patch = patch.object(subprocess, "run", side_effect=(
            subprocess.CalledProcessError(1, "gh") if comment_fails else None
        ))
        with contextlib.chdir(temp), patch.dict(os.environ, env, clear=True), \
                patch.object(subprocess, "check_output", side_effect=fake_head), run_patch as mocked_run:
            try:
                exec(compile(embedded_python(WORKFLOW, POST_STEP), str(WORKFLOW), "exec"), {"__name__": "__main__"})
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
                return code, output_path.read_text(encoding="utf-8") if output_path.exists() else "", mocked_run.call_count, len(seen)
        return None, output_path.read_text(encoding="utf-8") if output_path.exists() else "", mocked_run.call_count, len(seen)


def execute_prompt(diff, *, head_sha=SHA, changed_files=1):
    with tempfile.TemporaryDirectory() as temp:
        output = Path(temp) / "outputs.txt"
        meta = json.dumps({"headRefOid": head_sha, "baseRefOid": BASE_SHA,
                           "changedFiles": changed_files, "url": f"https://github.com/{REPO}/pull/{PR}"})
        values = iter((meta, diff))
        calls = []

        def fake_check_output(*args, **kwargs):
            calls.append(args[0])
            return next(values)

        env = {
            "REPO": REPO, "PR_NUMBER": str(PR), "COPILOT_REQUEST_RESULT": "failure",
            "COPILOT_REQUEST_STATUS": "unavailable", "GITHUB_OUTPUT": str(output),
        }
        with contextlib.chdir(temp), patch.dict(os.environ, env, clear=True), \
                patch.object(subprocess, "check_output", side_effect=fake_check_output):
            exec(compile(embedded_python(WORKFLOW, BUILD_STEP), str(WORKFLOW), "exec"), {"__name__": "__main__"})
        return output.read_text(encoding="utf-8"), calls


def github_output_value(text, key):
    prefix = f"{key}="
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):]
    raise AssertionError(f"GITHUB_OUTPUT key missing: {key}")


def execute_gate(overrides=None):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "CLASSIFY_RESULT": "success", "APPLICABLE": "true",
        "REASON": "test", "REQUEST_RESULT": "failure", "REQUEST_STATUS": "unavailable",
        "REQUEST_STEP_OUTCOME": "failure", "ACCEPTABLE_WITHOUT_FALLBACK": "false",
        "FALLBACK_ALLOWED": "true", "POLICY_BLOCKED": "false", "CLAUDE_FALLBACK_RESULT": "success",
        "CLAUDE_FALLBACK_VALIDATED": "true", "CLAUDE_FALLBACK_VERDICT": "findings",
        "CLAUDE_FALLBACK_HEAD_SHA": SHA,
    }
    env.update(overrides or {})
    return subprocess.run(
        ["bash", "-e", "-u", "-o", "pipefail", "-c", embedded_run(WORKFLOW, GATE_STEP)],
        env=env, capture_output=True, text=True, check=False,
    )


class EmbeddedFallbackParserTests(unittest.TestCase):
    def test_same_head_success_result_passes_and_is_not_approval(self):
        code, outputs, comment_calls, head_checks = execute_post(text=response())
        self.assertIsNone(code)
        self.assertIn("review_validated=true", outputs)
        self.assertIn("review_verdict=pass", outputs)
        self.assertIn(f"reviewed_head_sha={SHA}", outputs)
        self.assertEqual((comment_calls, head_checks), (1, 2))

    def test_nonempty_findings_are_completed_review_evidence(self):
        code, outputs, comment_calls, _ = execute_post(text=response(verdict="findings"))
        self.assertIsNone(code)
        self.assertIn("review_verdict=findings", outputs)
        self.assertEqual(comment_calls, 1)

    def test_missing_output_marker_malformed_json_and_failed_result_do_not_post(self):
        cases = [
            {"execution_exists": False}, {"text": "No structured review."},
            {"text": response(malformed=True)}, {"text": response(result="incomplete", verdict="block")},
            {"raw_output": json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": response()}]}})},
            {"raw_output": "{broken json log\n"},
        ]
        for options in cases:
            with self.subTest(options=options):
                code, outputs, comment_calls, _ = execute_post(**options)
                self.assertNotEqual(code, None)
                self.assertEqual(outputs, "")
                self.assertEqual(comment_calls, 0)

    def test_stale_target_or_truncated_diff_do_not_post(self):
        for options in (
            {"text": response(head="b" * 40)}, {"text": response(truncated=True)},
            {"current_heads": ["b" * 40]},
        ):
            with self.subTest(options=options):
                code, outputs, comment_calls, _ = execute_post(**options)
                self.assertNotEqual(code, None)
                self.assertEqual(outputs, "")
                self.assertEqual(comment_calls, 0)

    def test_terminal_execution_error_overrides_valid_assistant_response(self):
        for terminal in (
            {"type": "result", "subtype": "error", "is_error": True},
            {"type": "result", "subtype": "success", "is_error": True},
            {"type": "result", "subtype": "error", "is_error": False},
        ):
            with self.subTest(terminal=terminal):
                code, outputs, comment_calls, _ = execute_post(text=response(), terminal=terminal)
                self.assertNotEqual(code, None)
                self.assertEqual((outputs, comment_calls), ("", 0))

    def test_trusted_builder_truncation_flag_overrides_model_claim(self):
        output, _ = execute_prompt("x" * 60001)
        self.assertIn(f"Required target_head_sha (exact): {SHA}", output)
        self.assertIn("head_sha=" + SHA, output)
        self.assertIn('DIFF_TRUNCATED: ${{ steps.fallback_context.outputs.diff_truncated }}', WORKFLOW.read_text(encoding="utf-8"))
        truncated = github_output_value(output, "diff_truncated")
        self.assertEqual(truncated, "true")
        code, outputs, comment_calls, _ = execute_post(text=response(truncated=False), diff_truncated=truncated)
        self.assertNotEqual(code, None)
        self.assertEqual((outputs, comment_calls), ("", 0))
        capped_output, _ = execute_prompt("small diff", changed_files=300)
        self.assertEqual(github_output_value(capped_output, "diff_truncated"), "true")

    def test_diff_fetch_is_pinned_to_metadata_sha_pair_during_head_aba(self):
        output, calls = execute_prompt("diff fetched for H1")
        self.assertEqual(calls[1], ["gh", "api", "-H", "Accept: application/vnd.github.diff",
                                    f"repos/{REPO}/compare/{BASE_SHA}...{SHA}"])
        self.assertFalse(any(call[:3] == ["gh", "pr", "diff"] for call in calls))
        self.assertIn("diff fetched for H1", output)
        # Simulate H1 -> H2 -> H1: the final live SHA is H1, but the diff remains the H1 comparison.
        code, result_outputs, comment_calls, _ = execute_post(
            text=response(head=SHA), current_heads=[SHA, SHA]
        )
        self.assertIsNone(code)
        self.assertIn(f"reviewed_head_sha={SHA}", result_outputs)
        self.assertEqual(comment_calls, 1)

    def test_head_change_during_comment_or_comment_failure_has_no_validated_output(self):
        code, outputs, comment_calls, _ = execute_post(current_heads=[SHA, "b" * 40])
        self.assertNotEqual(code, None)
        self.assertEqual((outputs, comment_calls), ("", 1))
        with self.assertRaises(subprocess.CalledProcessError):
            execute_post(comment_fails=True)


class EmbeddedGateTests(unittest.TestCase):
    def test_findings_verdict_completes_evidence_but_not_human_approval(self):
        result = execute_gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("not approval", result.stdout)

    def test_missing_result_or_model_failure_fails_closed(self):
        for overrides in (
            {"CLAUDE_FALLBACK_VALIDATED": ""},
            {"CLAUDE_FALLBACK_RESULT": "failure"},
        ):
            with self.subTest(overrides=overrides):
                self.assertNotEqual(execute_gate(overrides).returncode, 0)

    def test_request_changes_and_block_verdicts_fail_required_gate(self):
        for verdict in ("request_changes", "block"):
            with self.subTest(verdict=verdict):
                result = execute_gate({"CLAUDE_FALLBACK_VERDICT": verdict})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("blocks this required check", result.stdout)

    def test_direct_copilot_success_path_remains_valid(self):
        result = execute_gate({"ACCEPTABLE_WITHOUT_FALLBACK": "true"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
