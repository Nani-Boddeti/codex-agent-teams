---
description: Run a coordinated Codex agent team
argument-hint: [team task]
allowed-tools: Bash Read Glob Grep
---

# Agent Teams

Run a coordinated group of Codex exec teammates for the requested task.

The user invoked this command with: $ARGUMENTS

## Core behavior

This skill is a delegation wrapper. For every non-empty `$ARGUMENTS` request, route the user's task to the Agent Teams runner and let the team perform the work. Do not satisfy the request as a solo Codex answer, solo code edit, solo review, or solo research task.

The only work to do before launching the runner is:
- Parse flags and task text
- Ask for mode when `--mode` is missing
- Build and execute the `agent_team.py` command

After the runner finishes, report the team workspace, summarize `summary.md`, and mention teammate failures. If the runner cannot be found or fails before creating a usable workspace, report that failure instead of doing the task yourself.

The runner performs a final self-review phase after synthesis. It writes `self-review.md` with reusable learnings from the run. When the current workspace is the Agent Teams plugin repo and edits are allowed, this phase may update the skill/plugin docs or runner with small, general improvements learned from the completed run.

## Instructions

### Step 1 — Parse arguments

Check if the user already included `--mode` in `$ARGUMENTS`.

- If `--mode` is present, skip to Step 2.
- If `--mode` is NOT present, ask the user to choose a mode:

  > Which mode would you like to run?
  >
  > 1. **mvp** *(default)* — autonomous planning docs → implement → peer review → validate → fix → deliver
  > 2. **review** — read-only team review, no file edits
  > 3. **research** — read-only discovery and analysis
  > 4. **implement-plan** — implement existing plan → peer review/fix → validate/fix → synthesize → self-review
  >
  > Press Enter or type `1` to use the default (mvp).

  Map the user's answer to a `--mode` flag. If they press Enter or say "default", use `--mode mvp`.

### Step 2 — Build the command

Extract any additional flags the user included (`--team-size`, `--roles`, `--model`, `--max-fix-rounds`, `--agent-timeout-seconds`, `--idle-timeout-seconds`, `--no-edit`, `--pause-for-questions`, `--skip-peer-review`, `--dry-run`). Put only the plain task text in `--task`.

```bash
AGENT_TEAMS_SCRIPT="$(python3 - <<'PY'
from pathlib import Path

home = Path.home()
cache_root = home / ".codex" / "plugins" / "cache"
candidates = [
    home / ".codex" / "plugins" / "agent-teams" / "scripts" / "agent_team.py",
    home / ".agents" / "plugins" / "plugins" / "agent-teams" / "scripts" / "agent_team.py",
    Path.cwd() / "plugins" / "agent-teams" / "scripts" / "agent_team.py",
]
if cache_root.exists():
    candidates.extend(cache_root.glob("*/agent-teams/scripts/agent_team.py"))

for candidate in candidates:
    if candidate.exists():
        print(candidate)
        raise SystemExit(0)

raise SystemExit("agent-teams script not found. Reinstall or update the Agent Teams plugin.")
PY
)"

python3 "$AGENT_TEAMS_SCRIPT" \
  --task "<task text>" \
  --cwd "$PWD" \
  --mode <chosen-mode> \
  [additional flags]
```

### Step 3 — Report results

When the script finishes:
- Report the team workspace path
- Summarize the final `summary.md`
- Summarize `self-review.md` if it exists, including whether the skill updated itself
- List any teammate failures from `roster.json`

## Modes

| Mode | Edits | Description |
|---|---|---|
| `mvp` | ✅ yes | Planning docs → implement → peer-review loop → validate → fix loop → deliver. Default. |
| `review` | ❌ no | Parallel team review with peer round and lead synthesis |
| `research` | ❌ no | Lightweight discovery, defaults to 4 teammates including project-manager |
| `implement-plan` | ✅ yes | Implement an existing plan with peer-review/fix loop, validate/fix loop, synthesis, and self-review (requires `--allow-edit` for edits). |

## MVP Pipeline

When mode is `mvp`, the pipeline runs fully autonomously:

1. **Planning** — product-owner + dev-lead define initial requirements and architecture, then canonical docs are created: `requirements-plan.md`, `hld.md`, `lld.md`, and `implementation-plan.md` (read-only for code)
2. **Reporting checkpoints** — project-manager answers `questions.md` in `project-status.md`
3. **Implementation** — developers build in parallel, reading planning outputs (edits on)
4. **Peer review** — reviewers check the implementation against the Requirement Plan, HLD, LLD, and Implementation Plan before validation
5. **Review fix loop** — if peer review gives comments (`STATUS: CHANGES_REQUESTED`), developers address the comments and peer review runs again until all reviewers return `STATUS: APPROVED`
6. **Validation** — tester checks against task requirements and planning docs → `STATUS: PASS` or `STATUS: FAIL`
7. **Fix loop** — if validation fails, developers fix only what failed, then peer review repeats before re-validation (up to `--max-fix-rounds`, default 2)
8. **Synthesis** — lead writes the final MVP handoff document (`summary.md`)
9. **Self-review and learning** — final reviewer records reusable process learnings in `self-review.md`; when running inside the Agent Teams repo with edits enabled, it may make small updates to the skill/plugin based on those learnings

## Implement-Plan Pipeline

When mode is `implement-plan`, the pipeline runs:

1. **Implementation** — developers implement the existing plan/task instructions
2. **Peer review** — reviewers check the implementation before validation
3. **Review fix loop** — if peer review gives comments (`STATUS: CHANGES_REQUESTED`), developers address the comments and peer review runs again until all reviewers return `STATUS: APPROVED`
4. **Validation** — validators run tests and acceptance checks → `STATUS: PASS` or `STATUS: FAIL`
5. **Validation fix loop** — if validation fails, developers fix only what failed, then peer review repeats before re-validation (up to `--max-fix-rounds`, default 2)
6. **Synthesis** — lead writes the final delivery handoff (`summary.md`)
7. **Self-review and learning** — final reviewer writes `self-review.md` and may update the skill/plugin when running inside the Agent Teams repo with edits enabled

## Default Team

```
product-owner    Requirements, users, scope, acceptance criteria    → planning docs
dev-lead         Architecture, work breakdown, integration risks    → planning docs
project-manager  Progress, blockers, stakeholder questions          → reporting
developer-1      First implementation area or primary code path     → implement
developer-2      Second area or adjacent integration points         → implement
tester           Validation, tests, acceptance checks, risks        → validate
```

Each run creates `questions.md`; add mid-run questions there and the project-manager answers them in `project-status.md` after phase checkpoints. Use `--pause-for-questions` when the user wants the runner to stop at each checkpoint before the project-manager answers.

Keep team communication compact. Only key decisions, blockers, assumptions, and action requests should be passed between teammates through `Message to ...` sections. Do not forward verbose output; full details remain in source output files.

Stuck agents are bounded. The runner stops any Codex subprocess after `--agent-timeout-seconds` wall-clock time (default 1800) or `--idle-timeout-seconds` without output (default 600), then records a timeout status in `roster.json`.

Use `--team-size` (up to 11) to expand. Larger teams add: UX designer, security reviewer, DevOps engineer, second tester, technical writer.

## Options

```bash
--mode mvp                 # autonomous end-to-end delivery (default)
--mode review              # read-only team review
--mode research            # read-only discovery
--mode implement-plan --allow-edit
--max-fix-rounds 3         # mvp/implement-plan: max validation fix iterations (default 2)
--team-size 7
--roles team.roles.json
--model gpt-5.5
--agent-timeout-seconds 1800
--idle-timeout-seconds 600
--no-edit                  # disable edits even in mvp mode
--pause-for-questions      # stop at reporting checkpoints for user questions
--skip-peer-review        # skip peer review loop (including mvp pre-validation review)
--dry-run
```

## Examples

```text
$agent-teams build a CLI task manager with add, list, and done commands
$agent-teams --mode review review this codebase for auth risks
$agent-teams --mode research research the frontend architecture
$agent-teams --mode mvp --max-fix-rounds 3 build a REST API for user auth
$agent-teams --mode implement-plan --allow-edit implement phase 1 of the approved plan
```
