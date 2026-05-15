---
description: Run a coordinated Codex agent team
argument-hint: [team task]
allowed-tools: Bash Read Glob Grep
---

# Agent Teams

Run a coordinated group of Codex exec teammates for the requested task.

The user invoked this command with: $ARGUMENTS

## Instructions

### Step 1 — Parse arguments

Check if the user already included `--mode` in `$ARGUMENTS`.

- If `--mode` is present, skip to Step 2.
- If `--mode` is NOT present, ask the user to choose a mode:

  > Which mode would you like to run?
  >
  > 1. **mvp** *(default)* — autonomous plan → implement → validate → fix → deliver
  > 2. **review** — read-only team review, no file edits
  > 3. **research** — read-only discovery and analysis
  > 4. **implement-plan** — implement an existing plan (edits enabled)
  >
  > Press Enter or type `1` to use the default (mvp).

  Map the user's answer to a `--mode` flag. If they press Enter or say "default", use `--mode mvp`.

### Step 2 — Build the command

Extract any additional flags the user included (`--team-size`, `--roles`, `--model`, `--max-fix-rounds`, `--agent-timeout-seconds`, `--idle-timeout-seconds`, `--no-edit`, `--pause-for-questions`, `--dry-run`). Put only the plain task text in `--task`.

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
- List any teammate failures from `roster.json`

## Modes

| Mode | Edits | Description |
|---|---|---|
| `mvp` | ✅ yes | Plan → implement → validate → fix loop → deliver. Default. |
| `review` | ❌ no | Parallel team review with peer round and lead synthesis |
| `research` | ❌ no | Lightweight discovery, defaults to 4 teammates including project-manager |
| `implement-plan` | ✅ yes | Implement an existing plan (requires `--allow-edit`) |

## MVP Pipeline

When mode is `mvp`, the pipeline runs fully autonomously:

1. **Planning** — product-owner + dev-lead define requirements and architecture (read-only)
2. **Reporting checkpoints** — project-manager answers `questions.md` in `project-status.md`
3. **Implementation** — developers build in parallel, reading planning outputs (edits on)
4. **Validation** — tester checks against requirements → `STATUS: PASS` or `STATUS: FAIL`
5. **Fix loop** — if fail, developers fix only what failed (up to `--max-fix-rounds`, default 2)
6. **Synthesis** — lead writes the final MVP handoff document (`summary.md`)

## Default Team

```
product-owner    Requirements, users, scope, acceptance criteria    → planning
dev-lead         Architecture, work breakdown, integration risks    → planning
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
--max-fix-rounds 3         # mvp: max fix iterations (default 2)
--team-size 7
--roles team.roles.json
--model gpt-5.5
--agent-timeout-seconds 1800
--idle-timeout-seconds 600
--no-edit                  # disable edits even in mvp mode
--pause-for-questions      # stop at reporting checkpoints for user questions
--skip-peer-review
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
