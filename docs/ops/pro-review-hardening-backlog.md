# Pro review hardening backlog

This document records B-scope hardening items for the ChatGPT Pro review handoff helper. They are not part of the current A-scope personal-local convenience-tool fix unless a future threat model explicitly treats local CLI arguments or local queue state as attacker-controlled.

## Current A-scope closure

The current A-scope fixes are intentionally minimal:

- F1 lightweight content filtering now catches generic secret assignments and JWT-shaped values while allowing simple mentions such as `set your api_key`, `password required`, and `access_token = None`.
- F4 base ref option injection is guarded by rejecting `--base-ref` values that start with `-` and separating the git revision from pathspecs with `--`.

## B-scope backlog

### F3 — `git status --short` parsing can include a wrong path

- Finding: `git_output()` returns `stdout.strip()`, and `changed_files()` parses porcelain status with `line[3:]`. A crafted or edge-case first line can be shifted so a non-changed or malformed path is included.
- PoC summary: reviewer reproduced a case where changed-file discovery included `'.txt'` even though it was not the intended changed path.
- Risk in A scope: low. The tool is personal-local and changed-file discovery only affects what context is bundled for review.
- Future fix direction: parse `git status --porcelain=v1 -z` or `--porcelain=v2 -z` with NUL delimiters instead of fixed slicing and stripped text.

### F8 — Windows device names remain allowed by `safe_segment()`

- Finding: newline, drive-ish values such as `C:foo`, and colon paths are rejected, but Windows reserved device names such as `CON`, `NUL`, `COM1`, and `LPT1` are still accepted as safe segments.
- Risk in A scope: low on the current macOS/Linux-oriented local workflow.
- Future fix direction: add a small case-insensitive denylist for Windows reserved device names if Windows support becomes in-scope.

### F11 — local attacker-controlled queue / CLI argument threat model

- Finding: several historical findings assume a local same-user attacker can control CLI args or `.ai-bridge/pro-review/queue.json`.
- Risk in A scope: intentionally downgraded because this helper is a personal-local convenience tool, not a multi-user service boundary.
- Future fix direction: if this becomes a shared daemon or team service, define a stronger authority model for queue producers, browser workers, and response writers before adding more local hardening.

### Architecture hardening — immutable request directories and permission guarantees

- Finding: request directories and artifacts are hardened against the confirmed symlink/path alias blockers, but the design is not a fully immutable content-addressed queue.
- Evidence: current request artifacts are regular files under `.ai-bridge/pro-review/requests/<request-id>/` with queue validation at claim/complete time.
- Risk in A scope: acceptable for personal-local use.
- Future fix direction:
  - content-address or digest-pin request artifacts;
  - add a begin-submit/heartbeat stage if browser workers become long-running shared infrastructure;
  - make request directory immutability an explicit invariant;
  - document and test file mode expectations such as directory `0700` and artifact `0600` across platforms.
