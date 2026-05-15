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

Extract any additional flags the user included (`--team-size`, `--roles`, `--model`, `--max-fix-rounds`, `--no-edit`, `--dry-run`). Put only the plain task text in `--task`.

```bash
python3 ~/.codex/plugins/agent-teams/scripts/agent_team.py \
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
| `research` | ❌ no | Lightweight discovery, defaults to 3 teammates |
| `implement-plan` | ✅ yes | Implement an existing plan (requires `--allow-edit`) |

## MVP Pipeline

When mode is `mvp`, the pipeline runs fully autonomously:

1. **Planning** — product-owner + dev-lead define requirements and architecture (read-only)
2. **Implementation** — developers build in parallel, reading planning outputs (edits on)
3. **Validation** — tester checks against requirements → `STATUS: PASS` or `STATUS: FAIL`
4. **Fix loop** — if fail, developers fix only what failed (up to `--max-fix-rounds`, default 2)
5. **Synthesis** — lead writes the final MVP handoff document (`summary.md`)

## Default Team

```
product-owner    Requirements, users, scope, acceptance criteria    → planning
dev-lead         Architecture, work breakdown, integration risks    → planning
developer-1      First implementation area or primary code path     → implement
developer-2      Second area or adjacent integration points         → implement
tester           Validation, tests, acceptance checks, risks        → validate
```

Use `--team-size` (up to 10) to expand. Larger teams add: UX designer, security reviewer, DevOps engineer, second tester, technical writer.

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
--no-edit                  # disable edits even in mvp mode
--skip-peer-review
--dry-run
```

## Examples

```text
/agent-teams:team build a CLI task manager with add, list, and done commands
/agent-teams:team --mode review review this codebase for auth risks
/agent-teams:team --mode research research the frontend architecture
/agent-teams:team --mode mvp --max-fix-rounds 3 build a REST API for user auth
/agent-teams:team --mode implement-plan --allow-edit implement phase 1 of the approved plan
```
