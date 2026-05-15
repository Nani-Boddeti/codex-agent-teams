# Agent Teams for Codex

Autonomous multi-agent delivery for Codex. Give it a task — a product idea, a feature, a bug fix — and a coordinated team of Codex agents plans, implements, validates, and delivers a working result with no user interaction in between.

## Install

**Option 1 — Codex marketplace (recommended):**

```bash
codex plugin marketplace add Nani-Boddeti/codex-agent-teams
```

Then open Codex → `/plugins` → find **Agent Teams** → Install. Restart Codex when prompted.

To uninstall, go to Codex → `/plugins` → **Agent Teams** → Uninstall, then run:

```bash
codex plugin marketplace remove agent-teams-marketplace
```

**Option 2 — Headless one-liner (no UI required):**

```bash
curl -fsSL https://raw.githubusercontent.com/Nani-Boddeti/codex-agent-teams/main/scripts/install_local.py | python3 - --force
```

Restart Codex after the command finishes. In Codex, invoke the plugin from the composer with `$agent-teams` or by selecting **Agent Teams** from the plugin picker.

## Usage

```text
$agent-teams build a CLI task manager with add, list, and done commands
```

If you don't specify `--mode`, the plugin asks you to choose:

```
Which mode would you like to run?

1. mvp (default) — autonomous plan → implement → validate → fix → deliver
2. review        — read-only team review, no file edits
3. research      — read-only discovery and analysis
4. implement-plan — implement an existing plan (edits enabled)

Press Enter or type 1 to use the default (mvp).
```

### MVP pipeline (default)

```
Phase 1 — Planning    product-owner + dev-lead define requirements and architecture
           Reporting  project-manager answers questions in project-status.md at checkpoints
Phase 2 — Implement   developers build in parallel, reading the planning outputs
Phase 3 — Validate    tester checks against requirements → STATUS: PASS or STATUS: FAIL
           Fix loop   if FAIL, developers fix only what failed (up to --max-fix-rounds)
Phase 4 — Synthesis   lead writes the final MVP handoff document (summary.md)
```

### More examples

```text
$agent-teams build a REST API for user authentication
$agent-teams add dark mode to this app
$agent-teams --mode review review this codebase for auth risks
$agent-teams --mode mvp --max-fix-rounds 3 build a real-time chat feature
```

### Run directly

```bash
python3 plugins/agent-teams/scripts/agent_team.py --task "build a CLI task manager" --cwd "$PWD"
```

### All options

```bash
--mode mvp                 # default — autonomous plan→implement→validate→deliver
--mode review              # read-only team review, no edits
--mode research            # read-only discovery and analysis
--mode implement-plan      # implement an existing plan (requires --allow-edit)
--max-fix-rounds 2         # max fix iterations before synthesis (default 2)
--team-size 7              # number of teammates, or 'auto'
--roles team.roles.json    # custom role profile (JSON file or inline JSON)
--model gpt-5.5            # Codex model override
--agent-timeout-seconds 1800 # stop any one agent after this wall-clock time
--idle-timeout-seconds 600   # stop any one agent after no output for this long
--no-edit                  # disable file edits even in mvp/implement-plan mode
--skip-peer-review         # skip peer review round (non-mvp modes)
--pause-for-questions      # pause at PM checkpoints so you can add questions
--dry-run                  # create workspace and prompts without running Codex
--state-dir .agent-teams/runs
```

## Default Team

Six teammates by default:

```
product-owner    Requirements, users, scope, acceptance criteria       → planning phase
dev-lead         Architecture, work breakdown, integration risks        → planning phase
project-manager  Progress reports, blockers, stakeholder questions     → reporting checkpoints
developer-1      First implementation area or primary code path         → implement phase
developer-2      Second implementation area or adjacent integration     → implement phase
tester           Validation, tests, acceptance checks, release risks    → validate phase
```

The runner creates `questions.md` in each workspace. Add questions there while the team is running; the project-manager teammate answers them in `project-status.md` and `reports/` after major phase checkpoints. Use `--pause-for-questions` when you want the runner to stop at each checkpoint before the PM answers.

Team communication is intentionally compact: `messages.md` keeps only key decisions, blockers, assumptions, and action requests that other teammates need. Full teammate output stays in `outbox/`, `reviews/`, `phases/`, and `reports/`.

Long-running or stuck agents are bounded. By default, each Codex subprocess is stopped after 30 minutes total or 10 minutes without output. Timeouts are recorded in the relevant output file, raw log, `tasks.json`, and `roster.json`.

Roles are assigned to phases automatically based on their name. Add up to 11 teammates with `--team-size`; larger teams add UX designer, security reviewer, DevOps engineer, second tester, and technical writer.

## Custom Roles

Pass `--roles` as a JSON file path or inline JSON:

```json
{
  "teammates": [
    {
      "name": "product-owner",
      "role": "Product owner",
      "objective": "Define users, workflows, and acceptance criteria for: {task}",
      "deliverable": "Product requirements and acceptance checklist."
    },
    {
      "name": "mobile-dev",
      "role": "Flutter developer",
      "objective": "Own mobile app implementation for: {task}",
      "deliverable": "Implementation notes, changed files, and validation."
    },
    {
      "name": "api-dev",
      "role": "API developer",
      "objective": "Own backend APIs and data contracts for: {task}",
      "deliverable": "API implementation notes, changed files, and validation."
    },
    {
      "name": "qa",
      "role": "Tester subagent",
      "objective": "Own test planning and validation for: {task}",
      "deliverable": "Test report with commands, failures, and remaining risks."
    }
  ]
}
```

```bash
python3 plugins/agent-teams/scripts/agent_team.py \
  --task "build the project dashboard MVP" \
  --cwd "$PWD" \
  --roles team.roles.json
```

## Run Output

Each run writes a workspace under `.agent-teams/runs/<timestamp>-<task-slug>/`:

```
task.md                          Original task and run settings
roster.json                      Teammate status and return codes
tasks.json                       Assignment details
messages.md                      Indexed teammate messages
questions.md                     Stakeholder questions for project-manager checkpoints
project-status.md                Latest project-manager status and answers
phases/planning/<name>.md        Planning phase outputs
phases/implement/<name>.md       Implementation outputs
phases/validate-0/<name>.md      Validation report (round 0)
phases/fix-1/<name>.md           Fix round outputs (if needed)
phases/validate-1/<name>.md      Re-validation after fix
reports/<checkpoint>-<name>.md   Project-manager status reports and answers
logs/<phase>-<name>.jsonl        Raw Codex JSONL events
summary.md                       Final MVP delivery document
```

## Verify

```bash
python3 scripts/validate.py
python3 plugins/agent-teams/scripts/agent_team.py --task "dry run" --cwd "$PWD" --dry-run
```

## Project Layout

```
.agents/plugins/marketplace.json
plugins/agent-teams/.codex-plugin/plugin.json
plugins/agent-teams/skills/team/SKILL.md
plugins/agent-teams/scripts/agent_team.py
plugins/agent-teams/assets/icon.svg
scripts/install_local.py
scripts/validate.py
```
