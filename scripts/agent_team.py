#!/usr/bin/env python3
"""Coordinate multiple Codex exec sessions as an agent team."""

from __future__ import annotations

import argparse
import json
import os
import re
import select
import signal
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any


MODES = ("review", "research", "implement-plan", "mvp")
DEFAULT_TEAM_SIZE = 6
DEFAULT_RESEARCH_TEAM_SIZE = 4
MAX_TEAM_SIZE = 11
MIN_TEAM_SIZE = 2
STATUS_LOCK = Lock()
TIMEOUT_RETURN_CODE = 124
TERMINATION_GRACE_SECONDS = 5

DEFAULT_ROLE_ARCHITECTURE = [
    {
        "name": "product-owner",
        "role": "Product owner",
        "objective": "Clarify the product goal, target users, acceptance criteria, and scope tradeoffs for: {task}",
        "deliverable": "Product brief with user workflows, priorities, and acceptance criteria.",
    },
    {
        "name": "dev-lead",
        "role": "Development lead",
        "objective": "Design the technical approach, implementation order, integration boundaries, and ownership split for: {task}",
        "deliverable": "Technical plan with architecture decisions, work breakdown, and integration risks.",
    },
    {
        "name": "project-manager",
        "role": "Project manager",
        "objective": "Track progress, open questions, blockers, decisions, and cross-teammate communication for: {task}",
        "deliverable": "Project status report that answers user questions and identifies next decisions or blockers.",
    },
    {
        "name": "developer-1",
        "role": "Developer subagent",
        "objective": "Work on the first implementation area or inspect the primary code path for: {task}",
        "deliverable": "Scoped implementation notes or changes with paths and validation.",
    },
    {
        "name": "developer-2",
        "role": "Developer subagent",
        "objective": "Work on the second implementation area or inspect adjacent integration points for: {task}",
        "deliverable": "Scoped implementation notes or changes with paths and validation.",
    },
    {
        "name": "tester",
        "role": "Tester subagent",
        "objective": "Plan and run validation, identify missing tests, and check user-facing acceptance criteria for: {task}",
        "deliverable": "Validation report with commands run, failures, coverage gaps, and release risks.",
    },
    {
        "name": "ux-designer",
        "role": "UX designer",
        "objective": "Review workflows, information architecture, interaction details, and accessibility needs for: {task}",
        "deliverable": "UX recommendations and acceptance criteria for the user experience.",
    },
    {
        "name": "security-reviewer",
        "role": "Security reviewer",
        "objective": "Review auth, data handling, secrets, dependency, and abuse-case risks for: {task}",
        "deliverable": "Security findings with severity, affected paths, and mitigations.",
    },
    {
        "name": "devops-engineer",
        "role": "DevOps engineer",
        "objective": "Review deployment, environment, CI, observability, and operational readiness for: {task}",
        "deliverable": "Operations checklist with deployment and monitoring risks.",
    },
    {
        "name": "tester-2",
        "role": "Tester subagent",
        "objective": "Perform deeper edge-case, regression, and failure-mode validation for: {task}",
        "deliverable": "Extended test report with edge cases and regression risks.",
    },
    {
        "name": "technical-writer",
        "role": "Technical writer",
        "objective": "Review documentation, onboarding, setup steps, and user-facing release notes for: {task}",
        "deliverable": "Documentation update plan with exact files or sections to change.",
    },
]


@dataclass
class Teammate:
    name: str
    role: str
    objective: str
    deliverable: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch a coordinated team of Codex exec teammates."
    )
    parser.add_argument("--task", required=True, help="Task for the agent team.")
    parser.add_argument(
        "--cwd",
        default=os.getcwd(),
        help="Project directory where Codex teammates should run.",
    )
    parser.add_argument(
        "--state-dir",
        default=".agent-teams/runs",
        help="Directory for team run workspaces, relative to cwd unless absolute.",
    )
    parser.add_argument(
        "--team-size",
        default="auto",
        help="Number of teammates to launch, or 'auto'. Bounded to 2-11.",
    )
    parser.add_argument(
        "--roles",
        help=(
            "Optional JSON file path or inline JSON role profile. Use either a list "
            "or an object with a 'teammates' list containing name, role, objective, "
            "and deliverable fields."
        ),
    )
    parser.add_argument("--model", help="Optional Codex model override.")
    parser.add_argument(
        "--mode",
        choices=MODES,
        default="mvp",
        help="Team operating mode. Default is mvp. review and research are no-edit modes.",
    )
    parser.add_argument(
        "--allow-edit",
        action="store_true",
        help="Allow teammates to edit files. Required for implement-plan edits.",
    )
    parser.add_argument(
        "--no-edit",
        action="store_true",
        default=False,
        help="Force teammates to avoid edits even in implement-plan mode.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Create workspace and prompts without launching Codex.",
    )
    parser.add_argument(
        "--skip-peer-review",
        action="store_true",
        help="Skip the teammate peer-review round.",
    )
    parser.add_argument(
        "--pause-for-questions",
        action="store_true",
        help="Pause at reporting checkpoints so you can add questions to questions.md before the project-manager answers.",
    )
    parser.add_argument(
        "--agent-timeout-seconds",
        type=int,
        default=1800,
        help="Maximum wall-clock seconds for each Codex agent subprocess. Use 0 to disable.",
    )
    parser.add_argument(
        "--idle-timeout-seconds",
        type=int,
        default=600,
        help="Maximum seconds without agent output before the subprocess is stopped. Use 0 to disable.",
    )
    parser.add_argument(
        "--max-fix-rounds",
        type=int,
        default=2,
        help="Maximum fix iterations in mvp mode before proceeding to synthesis (default 2).",
    )
    return parser.parse_args()


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return slug[:48] or "team-task"


def resolve_team_size(value: str, mode: str, role_profile_size: int | None = None) -> int:
    if role_profile_size is not None:
        if role_profile_size < MIN_TEAM_SIZE or role_profile_size > MAX_TEAM_SIZE:
            raise SystemExit(f"--roles must define between {MIN_TEAM_SIZE} and {MAX_TEAM_SIZE} teammates")
        if value != "auto":
            try:
                requested_size = int(value)
            except ValueError:
                raise SystemExit("--team-size must be 'auto' or match the number of teammates in --roles")
            if requested_size != role_profile_size:
                raise SystemExit("--team-size must match the number of teammates in --roles")
        return role_profile_size
    if value == "auto":
        return DEFAULT_RESEARCH_TEAM_SIZE if mode == "research" else DEFAULT_TEAM_SIZE
    try:
        size = int(value)
    except ValueError:
        raise SystemExit(f"--team-size must be 'auto' or an integer from {MIN_TEAM_SIZE} to {MAX_TEAM_SIZE}")
    if size < MIN_TEAM_SIZE or size > MAX_TEAM_SIZE:
        raise SystemExit(f"--team-size must be between {MIN_TEAM_SIZE} and {MAX_TEAM_SIZE}")
    return size


def teammate_from_entry(entry: dict[str, Any], index: int, task: str) -> Teammate:
    name = slugify(str(entry.get("name") or f"teammate-{index}"))
    role = str(entry.get("role") or entry.get("title") or "Teammate")
    objective = str(
        entry.get("objective")
        or f"Contribute to the task from the perspective of {role}: {{task}}"
    ).replace("{task}", task)
    deliverable = str(entry.get("deliverable") or f"{role} findings and recommendations.")
    return Teammate(name=name, role=role, objective=objective, deliverable=deliverable)


def default_role_profile(task: str, team_size: int) -> list[Teammate]:
    return [
        teammate_from_entry(entry, index, task)
        for index, entry in enumerate(DEFAULT_ROLE_ARCHITECTURE[:team_size], start=1)
    ]


def load_role_profile(value: str | None, task: str) -> list[Teammate] | None:
    if not value:
        return None
    source = Path(value).expanduser()
    if source.exists():
        raw = source.read_text(encoding="utf-8")
    else:
        raw = value
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--roles must be a JSON file path or inline JSON: {exc}") from exc

    entries = data.get("teammates") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise SystemExit("--roles JSON must be a list or an object with a 'teammates' list")

    teammates = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise SystemExit("Each --roles teammate entry must be an object")
        teammate = teammate_from_entry(entry, index, task)
        if teammate.name in seen:
            raise SystemExit(f"Duplicate teammate name in --roles: {teammate.name}")
        seen.add(teammate.name)
        teammates.append(teammate)
    return teammates


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def codex_base_command(args: argparse.Namespace) -> list[str]:
    cmd = ["codex", "exec", "--skip-git-repo-check", "--cd", str(Path(args.cwd).resolve()), "--json"]
    if args.model:
        cmd.extend(["--model", args.model])
    return cmd


def _positive_timeout(value: int, name: str) -> int:
    if value < 0:
        raise SystemExit(f"{name} must be 0 or greater")
    return value


def terminate_process(proc: subprocess.Popen[str], log_file: Any, reason: str) -> None:
    message = f"\n[agent-teams] stopping Codex subprocess: {reason}\n"
    log_file.write(message)
    log_file.flush()
    print(message, end="", file=sys.stderr)

    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        proc.terminate()

    try:
        proc.wait(timeout=TERMINATION_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except Exception:
        proc.kill()
    proc.wait()


def write_timeout_output(output_path: Path, reason: str) -> None:
    if output_path.exists() and output_path.stat().st_size > 0:
        return
    output_path.write_text(
        "STATUS: TIMED OUT\n\n"
        f"The Codex agent subprocess was stopped by Agent Teams: {reason}\n",
        encoding="utf-8",
    )


def run_codex(
    args: argparse.Namespace,
    prompt: str,
    output_path: Path,
    log_path: Path,
) -> int:
    cmd = codex_base_command(args)
    cmd.extend(["--output-last-message", str(output_path), "-"])

    with subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        start_new_session=True,
    ) as proc:
        assert proc.stdin is not None
        proc.stdin.write(prompt)
        proc.stdin.close()

        started_at = time.monotonic()
        last_output_at = started_at
        wall_timeout = _positive_timeout(args.agent_timeout_seconds, "--agent-timeout-seconds")
        idle_timeout = _positive_timeout(args.idle_timeout_seconds, "--idle-timeout-seconds")
        with log_path.open("w", encoding="utf-8") as log_file:
            assert proc.stdout is not None
            while True:
                now = time.monotonic()
                if wall_timeout and now - started_at > wall_timeout:
                    reason = f"wall-clock timeout after {wall_timeout}s"
                    terminate_process(proc, log_file, reason)
                    write_timeout_output(output_path, reason)
                    return TIMEOUT_RETURN_CODE
                if idle_timeout and now - last_output_at > idle_timeout:
                    reason = f"idle timeout after {idle_timeout}s without output"
                    terminate_process(proc, log_file, reason)
                    write_timeout_output(output_path, reason)
                    return TIMEOUT_RETURN_CODE

                if proc.poll() is not None:
                    for line in proc.stdout:
                        log_file.write(line)
                        print(line, end="")
                    log_file.flush()
                    break

                timeout_candidates = [1.0]
                if wall_timeout:
                    timeout_candidates.append(max(0.0, wall_timeout - (now - started_at)))
                if idle_timeout:
                    timeout_candidates.append(max(0.0, idle_timeout - (now - last_output_at)))
                ready, _, _ = select.select([proc.stdout], [], [], min(timeout_candidates))
                if not ready:
                    continue
                line = proc.stdout.readline()
                if not line:
                    continue
                last_output_at = time.monotonic()
                log_file.write(line)
                log_file.flush()
                print(line, end="")
        return proc.wait()


def role_architecture_text(teammates: list[Teammate]) -> str:
    return "\n".join(
        (
            f"- name: {teammate.name}; role: {teammate.role}; "
            f"objective focus: {teammate.objective}; deliverable: {teammate.deliverable}"
        )
        for teammate in teammates
    )


def plan_prompt(
    task: str,
    mode: str,
    team_size: int,
    edit_allowed: bool,
    role_profile: list[Teammate],
) -> str:
    edit_policy = "edits are allowed" if edit_allowed else "do not edit files"
    return f"""You are the lead for a Codex agent team.

Split the task below into exactly {team_size} independent teammate assignments.
Mode: {mode}
Edit policy: {edit_policy}

Use this team architecture. Keep these teammate names and roles exactly, but tailor each objective and deliverable to the task:
{role_architecture_text(role_profile)}

Return only valid JSON with this shape:
{{
  "teammates": [
    {{
      "name": "short-lowercase-hyphen-name",
      "role": "short role title",
      "objective": "specific objective",
      "deliverable": "specific final deliverable"
    }}
  ]
}}

Rules:
- Names must be unique and filesystem safe.
- Names and roles must match the team architecture above.
- Assignments must be parallelizable and avoid overlapping write ownership.
- In no-edit modes, every assignment must be read-only.
- Keep each objective concrete enough that a teammate can start immediately.

Task:
{task}
"""


def teammate_prompt(
    run_dir: Path,
    teammate: Teammate,
    mode: str,
    edit_allowed: bool,
) -> str:
    edit_policy = (
        "You may edit files only when necessary for your assignment."
        if edit_allowed
        else "Do not edit files. Read, inspect, test, and report only."
    )
    return f"""You are teammate `{teammate.name}` in a Codex agent team.

Role: {teammate.role}
Objective: {teammate.objective}
Deliverable: {teammate.deliverable}
Mode: {mode}
Edit policy: {edit_policy}

Before acting, read these coordination files:
- {run_dir / "task.md"}
- {run_dir / "tasks.json"}
- {run_dir / "inbox" / (teammate.name + ".md")}
- {run_dir / "messages.md"}

Work only on your assignment. Do not duplicate other teammates' work.
If editing is allowed, keep changes scoped to your assignment and do not revert user changes.

Communication requirements:
- If another teammate should know something, include a `Message to <teammate-name>:` section in your final response.
- If the whole team should know something, include a `Message to team:` section.
- Be concrete: include paths, decisions, blockers, or assumptions.

Final response requirements:
- Start with your teammate name and status.
- Summarize what you inspected or changed.
- List concrete findings, file paths, risks, and validation results.
- Mention only key decisions, blockers, assumptions, or required actions another teammate or the lead must know.
- Keep `Message to ...` sections short. Do not include verbose logs, full status dumps, or broad summaries there.
"""


def peer_review_prompt(
    run_dir: Path,
    reviewer: Teammate,
    review_targets: list[Teammate],
    mode: str,
    edit_allowed: bool,
) -> str:
    target_files = "\n".join(
        f"- {target.name}: {run_dir / 'outbox' / (target.name + '.md')}"
        for target in review_targets
        if target.name != reviewer.name
    )
    edit_policy = (
        "Do not make new edits during peer review unless fixing a small issue in your own prior work is essential."
        if edit_allowed
        else "Do not edit files during peer review."
    )
    return f"""You are teammate `{reviewer.name}` in the peer-review round.

Mode: {mode}
Edit policy: {edit_policy}

Read:
- {run_dir / "task.md"}
- {run_dir / "tasks.json"}
- {run_dir / "messages.md"}
- Your initial output: {run_dir / "outbox" / (reviewer.name + ".md")}
- Other teammate outputs:
{target_files}

Peer review goals:
- Find gaps, contradictions, duplicated assumptions, weak validation, and missed risks.
- Identify where another teammate's output improves or changes your own conclusion.
- Send only concrete decisions or required actions using `Message to <teammate-name>:` sections.
- Keep teammate messages brief. Do not forward verbose output or general commentary.
- Prefer synthesis and quality improvement over style critique.

Final response requirements:
- Start with `{reviewer.name} peer review`.
- List approved points, concerns, and corrections.
- Include specific file paths or evidence when relevant.
- End with any `Message to ...` sections needed for teammate communication.
"""


def synthesis_prompt(run_dir: Path, mode: str, edit_allowed: bool) -> str:
    return f"""You are the lead for a Codex agent team.

Read the shared team workspace and synthesize the final answer.
Workspace: {run_dir}
Mode: {mode}
Edits allowed: {edit_allowed}

Read:
- task.md
- tasks.json
- roster.json
- every file in outbox/
- every file in reviews/
- messages.md

Write a concise final synthesis that includes:
- Overall outcome
- Important findings or changes
- Peer-review corrections or conflicts that changed the conclusion
- Teammate failures, if any
- Validation performed
- Recommended next steps only if they are directly useful
"""


_REPORTING_KEYWORDS = frozenset({"project manager", "project-manager", "program manager", "reporting", "coordinator"})
_PLANNING_KEYWORDS = frozenset({"owner", "lead", "architect", "ux", "design", "product"})
_VALIDATION_KEYWORDS = frozenset({"test", "qa", "security", "audit"})


def is_reporting_teammate(teammate: Teammate) -> bool:
    key = (teammate.name + " " + teammate.role).lower()
    return any(keyword in key for keyword in _REPORTING_KEYWORDS)


def categorize_for_mvp(
    teammates: list[Teammate],
) -> tuple[list[Teammate], list[Teammate], list[Teammate], list[Teammate]]:
    """Split teammates into (reporting, planning, implementation, validation)."""
    reporting = [t for t in teammates if is_reporting_teammate(t)]
    phase_teammates = [t for t in teammates if not is_reporting_teammate(t)]
    planning: list[Teammate] = []
    implementation: list[Teammate] = []
    validation: list[Teammate] = []
    for t in phase_teammates:
        key = (t.name + " " + t.role).lower()
        if any(k in key for k in _VALIDATION_KEYWORDS):
            validation.append(t)
        elif any(k in key for k in _PLANNING_KEYWORDS):
            planning.append(t)
        else:
            implementation.append(t)
    if not validation and phase_teammates:
        validation = [phase_teammates[-1]]
        implementation = [t for t in implementation if t.name != phase_teammates[-1].name]
    if not planning and len(phase_teammates) > len(validation):
        n = min(2, len(phase_teammates) - len(validation))
        planning = [t for t in phase_teammates if t not in validation][:n]
        planning_names = {t.name for t in planning}
        implementation = [t for t in implementation if t.name not in planning_names]
    return reporting, planning, implementation, validation


def tester_passed(report_path: Path) -> bool:
    if not report_path.exists():
        return False
    for line in reversed(report_path.read_text(encoding="utf-8", errors="replace").splitlines()):
        stripped = line.strip().upper()
        if stripped == "STATUS: PASS":
            return True
        if stripped == "STATUS: FAIL":
            return False
    return False


def _mvp_planning_prompt(run_dir: Path, teammate: Teammate, task: str) -> str:
    return f"""You are teammate `{teammate.name}` in the PLANNING phase of an MVP build.

Role: {teammate.role}
Task: {task}

Edit policy: Do not edit code files. Define requirements and architecture only.

Read:
- {run_dir / "task.md"}

Your job:
- Define clear, implementable requirements or architectural decisions
- Identify technical constraints, integration points, and acceptance criteria
- Be concrete enough that developers can start implementing immediately

Output requirements:
- Start your response with your name and role
- Include a `Message to team:` section listing the top decisions developers must act on
- End with exactly: STATUS: PLANNING COMPLETE
"""


def _mvp_implement_prompt(
    run_dir: Path,
    teammate: Teammate,
    plan_dir: Path,
    fix_round: int = 0,
    tester_report: Path | None = None,
) -> str:
    planning_files = "\n".join(f"- {p}" for p in sorted(plan_dir.glob("*.md")) if p.exists())
    if fix_round and tester_report and tester_report.exists():
        context = f"""This is FIX ROUND {fix_round}. The tester found failures.

Tester report: {tester_report}

Read the tester report carefully. Fix ONLY what failed. Do not rewrite working code.
Scoped fix: change the minimum number of files needed to resolve the failures.
"""
    else:
        context = f"""This is the IMPLEMENTATION phase. Build the MVP based on planning outputs.

Planning outputs (read these first):
{planning_files}
"""
    return f"""You are teammate `{teammate.name}` in the {'FIX ROUND ' + str(fix_round) if fix_round else 'IMPLEMENTATION'} phase.

Role: {teammate.role}
Objective: {teammate.objective}
Deliverable: {teammate.deliverable}

{context}
Coordination files:
- {run_dir / "task.md"}
- {run_dir / "tasks.json"}
- {run_dir / "inbox" / (teammate.name + ".md")}
- {run_dir / "messages.md"}

Edit policy: You may edit files. Keep changes scoped to your assignment.
Do not revert other teammates' changes.

Output requirements:
- Start with your name and role
- List every file you created or modified with a brief reason
- Include a `Message to team:` section if you made decisions others need to know
- End with exactly: STATUS: DONE  (or STATUS: BLOCKED: <reason> if blocked)
"""


def _mvp_validation_prompt(
    run_dir: Path,
    teammate: Teammate,
    impl_dirs: list[Path],
    fix_round: int = 0,
) -> str:
    impl_files = "\n".join(
        f"- {p}" for d in impl_dirs for p in sorted(d.glob("*.md")) if p.exists()
    )
    round_label = f"after fix round {fix_round}" if fix_round else "initial validation"
    return f"""You are teammate `{teammate.name}` performing VALIDATION ({round_label}).

Role: {teammate.role}

Read:
- {run_dir / "task.md"}
- {run_dir / "tasks.json"}
- {run_dir / "messages.md"}
- Implementation outputs:
{impl_files}

Your job:
- Run tests and verify the implementation against the requirements in task.md
- Check that all acceptance criteria are met
- Report all failures with specific file paths, function names, and error messages

Group failures by severity:
- CRITICAL: blocks shipping the MVP
- MAJOR: significant gap but has a workaround
- MINOR: cosmetic or non-blocking

Output requirements:
- Start with your name and role
- List every check you ran with PASS/FAIL status
- List all failures grouped by severity with specific evidence
- End with exactly one of:
  STATUS: PASS  (no critical failures — MVP is shippable)
  STATUS: FAIL  (critical failures remain — fix round needed)
"""


def _mvp_synthesis_prompt(run_dir: Path, passed: bool, fix_rounds: int) -> str:
    outcome = "PASSED validation" if passed else f"reached max fix rounds ({fix_rounds}) without full pass"
    return f"""You are the lead for a Codex agent team. Synthesize the final MVP delivery.

Workspace: {run_dir}
Validation outcome: {outcome}
Fix rounds completed: {fix_rounds}

Read every file in the workspace:
- task.md — original requirements
- phases/planning/*.md — requirements and architecture decisions
- phases/implement/*.md — initial implementation outputs
- phases/validate-*/*.md — validation reports per round
- phases/fix-*/*.md — fix round outputs (if any)
- reports/*.md — project-manager status reports and stakeholder Q&A
- messages.md — team communication

Write a concise MVP delivery report covering:
1. What was built (files created/modified, features implemented)
2. Validation results (what passed, what failed if anything remains)
3. Known gaps or recommended follow-up work
4. How to run or use what was built

Be direct and actionable. This is the handoff document.
"""


def project_manager_prompt(
    run_dir: Path,
    teammate: Teammate,
    phase: str,
    mode: str,
    edit_allowed: bool,
) -> str:
    return f"""You are teammate `{teammate.name}`, the project manager for this Codex agent team.

Role: {teammate.role}
Mode: {mode}
Edits allowed for delivery teammates: {edit_allowed}
Current checkpoint: {phase}

Read the shared workspace before answering:
- {run_dir / "task.md"}
- {run_dir / "tasks.json"}
- {run_dir / "roster.json"}
- {run_dir / "messages.md"}
- {run_dir / "questions.md"}
- all relevant files under {run_dir / "phases"}
- prior reports under {run_dir / "reports"}

Your job:
- Give a concise project status update for the current checkpoint.
- Answer any stakeholder questions listed in `questions.md`.
- Call out blockers, risks, decisions made, and decisions still needed.
- Route unresolved technical questions to the teammate best placed to answer them.
- Forward only key decisions, blockers, and action requests to teammates.
- Do not edit project code. This is reporting and coordination only.

Final response requirements:
- Start with `{teammate.name} status: {phase}`.
- Include sections: Progress, Answers, Blockers/Risks, Next.
- Use concrete paths and teammate names when useful.
- If there are no open stakeholder questions, say so under Answers.
- Include `Message to team:` only when the whole team needs to act on something, and keep it to key decisions/actions.
"""


def run_project_manager_reports(
    args: argparse.Namespace,
    run_dir: Path,
    reporting: list[Teammate],
    phase: str,
    mode: str,
    edit_allowed: bool,
) -> None:
    if not reporting:
        return
    reports_dir = run_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    phase_slug = slugify(phase)
    for teammate in reporting:
        output = reports_dir / f"{phase_slug}-{teammate.name}.md"
        latest = run_dir / "project-status.md"
        if args.dry_run:
            output.write_text(
                f"{teammate.name} status: {phase}\n\nAnswers: No dry-run questions answered.\n",
                encoding="utf-8",
            )
            latest.write_text(output.read_text(encoding="utf-8"), encoding="utf-8")
            update_status(run_dir, teammate.name, f"reporting-{phase_slug}-dry-run", 0)
            continue
        update_status(run_dir, teammate.name, f"reporting-{phase_slug}")
        rc = run_codex(
            args,
            project_manager_prompt(run_dir, teammate, phase, mode, edit_allowed),
            output,
            run_dir / "logs" / f"report-{phase_slug}-{teammate.name}.jsonl",
        )
        latest.write_text(output.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        append_messages_from_paths(run_dir, [output], f"report {phase}")
        update_status(run_dir, teammate.name, "reporting-completed" if rc == 0 else "reporting-failed", rc)
        print(f"  {teammate.name} report for {phase} done (rc={rc})")


def pause_for_questions(args: argparse.Namespace, run_dir: Path, phase: str) -> None:
    if not args.pause_for_questions or args.dry_run:
        return
    print(
        "\nProject-manager checkpoint: "
        f"{phase}. Add questions to {run_dir / 'questions.md'}, then press Enter to continue."
    )
    try:
        input()
    except EOFError:
        print("No interactive input available; continuing without pausing.")


def status_from_returncode(phase: str, returncode: int) -> str:
    if returncode == 0:
        return f"{phase}-completed"
    if returncode == TIMEOUT_RETURN_CODE:
        return f"{phase}-timed-out"
    return f"{phase}-failed"


def run_phase_agent(
    args: argparse.Namespace,
    run_dir: Path,
    teammate: Teammate,
    phase: str,
    prompt: str,
    output_path: Path,
    log_path: Path,
) -> int:
    update_status(run_dir, teammate.name, f"{phase}-running")
    returncode = run_codex(args, prompt, output_path, log_path)
    update_status(run_dir, teammate.name, status_from_returncode(phase, returncode), returncode)
    return returncode


def run_mvp_pipeline(
    args: argparse.Namespace,
    teammates: list[Teammate],
    run_dir: Path,
) -> int:
    max_fix_rounds: int = args.max_fix_rounds
    edit_allowed = not args.no_edit
    reporting, planning, impl_team, validators = categorize_for_mvp(teammates)

    print(
        f"\nMVP pipeline: {len(reporting)} reporting | "
        f"{len(planning)} planning | "
        f"{len(impl_team)} implementing | "
        f"{len(validators)} validating | "
        f"up to {max_fix_rounds} fix rounds"
    )

    # ── Phase 1: Planning ──────────────────────────────────────────────────────
    print("\n=== Phase 1: Planning ===")
    plan_dir = run_dir / "phases" / "planning"
    plan_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        for t in planning:
            (plan_dir / f"{t.name}.md").write_text(
                f"Dry-run planning output for {t.name}.\n", encoding="utf-8"
            )
            update_status(run_dir, t.name, "planning-dry-run", 0)
    else:
        with ThreadPoolExecutor(max_workers=max(1, len(planning))) as executor:
            futures = {
                executor.submit(
                    run_phase_agent,
                    args,
                    run_dir,
                    t,
                    "planning",
                    _mvp_planning_prompt(run_dir, t, args.task),
                    plan_dir / f"{t.name}.md",
                    run_dir / "logs" / f"plan-{t.name}.jsonl",
                ): t
                for t in planning
            }
            for future in as_completed(futures):
                rc = future.result()
                print(f"  {futures[future].name} done (rc={rc})")

    append_messages(run_dir, str(plan_dir.relative_to(run_dir)))
    pause_for_questions(args, run_dir, "after planning")
    run_project_manager_reports(args, run_dir, reporting, "after planning", args.mode, edit_allowed)

    # ── Phases 2+: Implement → Validate → Fix loop ────────────────────────────
    fix_round = 0
    tester_report: Path | None = None
    passed = False

    while True:
        phase_key = f"fix-{fix_round}" if fix_round else "implement"
        print(f"\n=== {'Fix Round ' + str(fix_round) if fix_round else 'Phase 2: Implementation'} ===")
        impl_dir = run_dir / "phases" / phase_key
        impl_dir.mkdir(parents=True, exist_ok=True)

        if args.dry_run:
            for t in impl_team:
                (impl_dir / f"{t.name}.md").write_text(
                    f"Dry-run implementation for {t.name}.\n", encoding="utf-8"
                )
                update_status(run_dir, t.name, f"{phase_key}-dry-run", 0)
        else:
            with ThreadPoolExecutor(max_workers=max(1, len(impl_team))) as executor:
                futures = {
                    executor.submit(
                        run_phase_agent,
                        args,
                        run_dir,
                        t,
                        phase_key,
                        _mvp_implement_prompt(run_dir, t, plan_dir, fix_round, tester_report),
                        impl_dir / f"{t.name}.md",
                        run_dir / "logs" / f"{phase_key}-{t.name}.jsonl",
                    ): t
                    for t in impl_team
                }
                for future in as_completed(futures):
                    rc = future.result()
                    print(f"  {futures[future].name} done (rc={rc})")

        append_messages(run_dir, str(impl_dir.relative_to(run_dir)))
        pause_for_questions(args, run_dir, f"after {phase_key}")
        run_project_manager_reports(args, run_dir, reporting, f"after {phase_key}", args.mode, edit_allowed)

        # ── Validation ────────────────────────────────────────────────────────
        val_label = f"validate-{fix_round}"
        print(f"\n=== Validation (round {fix_round}) ===")
        val_dir = run_dir / "phases" / val_label
        val_dir.mkdir(parents=True, exist_ok=True)

        impl_dirs = [
            run_dir / "phases" / (f"fix-{r}" if r else "implement")
            for r in range(fix_round + 1)
        ]

        if args.dry_run:
            for v in validators:
                (val_dir / f"{v.name}.md").write_text(
                    "Dry-run validation.\nSTATUS: PASS\n", encoding="utf-8"
                )
                update_status(run_dir, v.name, f"{val_label}-dry-run", 0)
        else:
            for v in validators:
                rc = run_phase_agent(
                    args,
                    run_dir,
                    v,
                    val_label,
                    _mvp_validation_prompt(run_dir, v, impl_dirs, fix_round),
                    val_dir / f"{v.name}.md",
                    run_dir / "logs" / f"{val_label}-{v.name}.jsonl",
                )
                tester_report = val_dir / f"{v.name}.md"
                print(f"  {v.name} done (rc={rc})")

        append_messages(run_dir, str(val_dir.relative_to(run_dir)))
        pause_for_questions(args, run_dir, f"after {val_label}")
        run_project_manager_reports(args, run_dir, reporting, f"after {val_label}", args.mode, edit_allowed)

        passed = all(tester_passed(val_dir / f"{v.name}.md") for v in validators)

        if passed:
            print("Validation passed. Proceeding to synthesis.")
            break

        fix_round += 1
        if fix_round > max_fix_rounds:
            print(
                f"Max fix rounds ({max_fix_rounds}) reached. Proceeding to synthesis.",
                file=sys.stderr,
            )
            break

        print(f"Validation failed. Starting fix round {fix_round}.")

    # ── Final synthesis ────────────────────────────────────────────────────────
    print("\n=== Final Synthesis ===")
    summary = run_dir / "summary.md"
    summary_log = run_dir / "logs" / "lead-summary.jsonl"

    if args.dry_run:
        summary.write_text(
            f"# Dry-run MVP Summary\n\nWorkspace: {run_dir}\nPassed: {passed}\nFix rounds: {fix_round}\n",
            encoding="utf-8",
        )
    else:
        rc = run_codex(args, _mvp_synthesis_prompt(run_dir, passed, fix_round), summary, summary_log)
        if rc != 0:
            print("Lead synthesis failed; inspect phase outputs manually.", file=sys.stderr)
            return rc

    print(f"\nMVP delivery complete. Summary: {summary}")
    return 0


def parse_plan(plan_text: str, team_size: int) -> list[Teammate]:
    raw = plan_text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Lead did not return valid JSON: {exc}") from exc

    teammates = data.get("teammates")
    if not isinstance(teammates, list) or len(teammates) != team_size:
        raise SystemExit(f"Lead plan must contain exactly {team_size} teammates")

    parsed: list[Teammate] = []
    seen: set[str] = set()
    for index, item in enumerate(teammates, start=1):
        if not isinstance(item, dict):
            raise SystemExit("Each teammate entry must be an object")
        name = slugify(str(item.get("name") or f"teammate-{index}"))
        if name in seen:
            name = f"{name}-{index}"
        seen.add(name)
        parsed.append(
            Teammate(
                name=name,
                role=str(item.get("role") or "Teammate"),
                objective=str(item.get("objective") or "Investigate the assigned area."),
                deliverable=str(item.get("deliverable") or "Final findings."),
            )
        )
    return parsed


def fallback_plan(task: str, mode: str, team_size: int, role_profile: list[Teammate]) -> list[Teammate]:
    base = role_profile[:team_size]
    if mode == "research":
        base = [
            Teammate(
                base[0].name,
                base[0].role,
                f"Map the current state relevant to: {task}",
                "Current-state summary with source paths.",
            ),
            *base[1:],
        ]
    return base[:team_size]


def create_workspace(args: argparse.Namespace, team_size: int, edit_allowed: bool) -> Path:
    cwd = Path(args.cwd).resolve()
    state_dir = Path(args.state_dir).expanduser()
    if not state_dir.is_absolute():
        state_dir = cwd / state_dir
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000:06d}-{slugify(args.task)}"
    run_dir = state_dir / run_id
    for child in ("inbox", "outbox", "reviews", "logs", "reports", "phases"):
        (run_dir / child).mkdir(parents=True, exist_ok=True)

    task_md = f"""# Agent Team Task

Task:
{args.task}

Mode: {args.mode}
Team size: {team_size}
Edit allowed: {edit_allowed}
Project cwd: {cwd}
Created: {time.strftime("%Y-%m-%d %H:%M:%S %z")}
"""
    (run_dir / "task.md").write_text(task_md, encoding="utf-8")
    (run_dir / "messages.md").write_text(
        "# Team Messages\n\n"
        "Teammates communicate through `Message to <name>:` and `Message to team:` sections in outbox and review files. "
        "The runner indexes only compact key decisions, blockers, assumptions, and action requests here after each round. "
        "Verbose output stays in the source report files.\n",
        encoding="utf-8",
    )
    (run_dir / "questions.md").write_text(
        "# Stakeholder Questions\n\n"
        "Add questions here while the team is running. The project-manager teammate reads this file at phase checkpoints "
        "and answers in `project-status.md` and `reports/`.\n\n"
        "## Open Questions\n\n"
        "- None yet.\n",
        encoding="utf-8",
    )
    (run_dir / "project-status.md").write_text(
        "# Project Status\n\n"
        "The project-manager teammate updates this file after major phase checkpoints.\n",
        encoding="utf-8",
    )
    return run_dir


def write_assignments(run_dir: Path, teammates: list[Teammate]) -> None:
    tasks = {
        "teammates": [
            {
                "name": teammate.name,
                "role": teammate.role,
                "objective": teammate.objective,
                "deliverable": teammate.deliverable,
                "status": "pending",
            }
            for teammate in teammates
        ]
    }
    write_json(run_dir / "tasks.json", tasks)

    for teammate in teammates:
        inbox = f"""# Inbox: {teammate.name}

Role: {teammate.role}

Objective:
{teammate.objective}

Deliverable:
{teammate.deliverable}
"""
        (run_dir / "inbox" / f"{teammate.name}.md").write_text(inbox, encoding="utf-8")


def update_status(run_dir: Path, name: str, status: str, returncode: int | None = None) -> None:
    with STATUS_LOCK:
        roster_path = run_dir / "roster.json"
        roster = read_json(roster_path)
        for teammate in roster["teammates"]:
            if teammate["name"] == name:
                teammate["status"] = status
                teammate["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S %z")
                if returncode is not None:
                    teammate["returncode"] = returncode
        write_json(roster_path, roster)

        tasks_path = run_dir / "tasks.json"
        tasks = read_json(tasks_path)
        for teammate in tasks["teammates"]:
            if teammate["name"] == name:
                teammate["status"] = status
        write_json(tasks_path, tasks)


def run_teammate(
    args: argparse.Namespace,
    run_dir: Path,
    teammate: Teammate,
    edit_allowed: bool,
) -> tuple[str, int]:
    update_status(run_dir, teammate.name, "running")
    prompt = teammate_prompt(run_dir, teammate, args.mode, edit_allowed)
    outbox = run_dir / "outbox" / f"{teammate.name}.md"
    log = run_dir / "logs" / f"{teammate.name}.jsonl"
    if args.dry_run:
        outbox.write_text(
            f"Dry run for {teammate.name}: {teammate.objective}\n",
            encoding="utf-8",
        )
        update_status(run_dir, teammate.name, "dry-run", 0)
        return teammate.name, 0
    returncode = run_codex(args, prompt, outbox, log)
    update_status(run_dir, teammate.name, status_from_returncode("teammate", returncode), returncode)
    return teammate.name, returncode


def compact_message(message: str, max_lines: int = 8, max_chars: int = 1200) -> str:
    lines = [line.rstrip() for line in message.strip().splitlines()]
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped and (not kept or not kept[-1]):
            continue
        kept.append(line)
        if len(kept) >= max_lines:
            break
    compact = "\n".join(kept).strip()
    if len(compact) > max_chars:
        compact = compact[: max_chars - 3].rstrip() + "..."
    return compact


def extract_messages_from_paths(paths: list[Path]) -> list[str]:
    messages: list[str] = []
    for path in sorted(paths):
        text = path.read_text(encoding="utf-8", errors="replace")
        current: list[str] = []
        capture = False
        for line in text.splitlines():
            if re.match(r"^\s*Message to (team|[a-zA-Z0-9_.-]+)\s*:", line, re.IGNORECASE):
                if current:
                    messages.append("\n".join(current).strip())
                current = [f"From {path.stem}: {line.strip()}"]
                capture = True
                continue
            if capture:
                if re.match(r"^\s*#{1,6}\s+", line) or re.match(r"^\s*[A-Z][A-Za-z ]+:\s*$", line):
                    if current:
                        messages.append("\n".join(current).strip())
                    current = []
                    capture = False
                else:
                    current.append(line)
        if current:
            messages.append("\n".join(current).strip())
    return [compact_message(message) for message in messages if message]


def append_messages_from_paths(run_dir: Path, paths: list[Path], source_label: str) -> None:
    messages = extract_messages_from_paths(paths)
    if not messages:
        return
    with (run_dir / "messages.md").open("a", encoding="utf-8") as file:
        file.write(f"\n## Messages from {source_label}\n\n")
        for message in messages:
            file.write(message.strip() + "\n\n")


def extract_messages(run_dir: Path, source_dir: str) -> list[str]:
    return extract_messages_from_paths(list((run_dir / source_dir).glob("*.md")))


def append_messages(run_dir: Path, source_dir: str) -> None:
    append_messages_from_paths(run_dir, list((run_dir / source_dir).glob("*.md")), source_dir)


def run_peer_review(
    args: argparse.Namespace,
    run_dir: Path,
    reviewer: Teammate,
    teammates: list[Teammate],
    edit_allowed: bool,
) -> tuple[str, int]:
    update_status(run_dir, reviewer.name, "peer-reviewing")
    review = run_dir / "reviews" / f"{reviewer.name}.md"
    log = run_dir / "logs" / f"{reviewer.name}-peer-review.jsonl"
    if args.dry_run:
        review.write_text(
            f"Dry run peer review for {reviewer.name}.\n",
            encoding="utf-8",
        )
        update_status(run_dir, reviewer.name, "peer-review-dry-run", 0)
        return reviewer.name, 0
    prompt = peer_review_prompt(run_dir, reviewer, teammates, args.mode, edit_allowed)
    returncode = run_codex(args, prompt, review, log)
    update_status(
        run_dir,
        reviewer.name,
        status_from_returncode("peer-review", returncode),
        returncode,
    )
    return reviewer.name, returncode


def main() -> int:
    args = parse_args()
    _positive_timeout(args.agent_timeout_seconds, "--agent-timeout-seconds")
    _positive_timeout(args.idle_timeout_seconds, "--idle-timeout-seconds")
    if shutil.which("codex") is None and not args.dry_run:
        raise SystemExit("codex CLI was not found on PATH")

    cwd = Path(args.cwd).resolve()
    if not cwd.exists():
        raise SystemExit(f"--cwd does not exist: {cwd}")

    custom_role_profile = load_role_profile(args.roles, args.task)
    team_size = resolve_team_size(
        args.team_size,
        args.mode,
        len(custom_role_profile) if custom_role_profile is not None else None,
    )
    role_profile = custom_role_profile or default_role_profile(args.task, team_size)
    if args.mode == "mvp":
        edit_allowed = not args.no_edit
    else:
        edit_allowed = args.mode == "implement-plan" and args.allow_edit and not args.no_edit
    run_dir = create_workspace(args, team_size, edit_allowed)

    print(f"Agent team workspace: {run_dir}")

    lead_plan_output = run_dir / "plan.md"
    lead_plan_log = run_dir / "logs" / "lead-plan.jsonl"
    if args.dry_run:
        teammates = fallback_plan(args.task, args.mode, team_size, role_profile)
        lead_plan_output.write_text(
            json.dumps(
                {
                    "teammates": [
                        {
                            "name": t.name,
                            "role": t.role,
                            "objective": t.objective,
                            "deliverable": t.deliverable,
                        }
                        for t in teammates
                    ]
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    else:
        rc = run_codex(
            args,
            plan_prompt(args.task, args.mode, team_size, edit_allowed, role_profile),
            lead_plan_output,
            lead_plan_log,
        )
        if rc != 0:
            print("Lead planning failed; using fallback assignments.", file=sys.stderr)
            teammates = fallback_plan(args.task, args.mode, team_size, role_profile)
        else:
            teammates = parse_plan(lead_plan_output.read_text(encoding="utf-8"), team_size)

    if "teammates" not in locals():
        teammates = parse_plan(lead_plan_output.read_text(encoding="utf-8"), team_size)

    write_assignments(run_dir, teammates)
    roster = {
        "run_dir": str(run_dir),
        "mode": args.mode,
        "edit_allowed": edit_allowed,
        "teammates": [
            {
                "name": t.name,
                "role": t.role,
                "status": "pending",
                "returncode": None,
            }
            for t in teammates
        ],
    }
    write_json(run_dir / "roster.json", roster)

    if args.mode == "mvp":
        return run_mvp_pipeline(args, teammates, run_dir)

    reporting = [teammate for teammate in teammates if is_reporting_teammate(teammate)]
    execution_teammates = [teammate for teammate in teammates if not is_reporting_teammate(teammate)]
    if not execution_teammates:
        execution_teammates = teammates

    teammate_results: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=len(execution_teammates)) as executor:
        futures = [
            executor.submit(run_teammate, args, run_dir, teammate, edit_allowed)
            for teammate in execution_teammates
        ]
        for future in as_completed(futures):
            name, returncode = future.result()
            teammate_results[name] = returncode
            print(f"Teammate {name} finished with return code {returncode}")

    append_messages(run_dir, "outbox")
    pause_for_questions(args, run_dir, "after teammate round")
    run_project_manager_reports(args, run_dir, reporting, "after teammate round", args.mode, edit_allowed)

    review_teammates = [
        teammate for teammate in execution_teammates if teammate_results.get(teammate.name) == 0
    ]
    if not args.skip_peer_review and len(review_teammates) > 1:
        print("Starting teammate peer-review round.")
        with ThreadPoolExecutor(max_workers=len(review_teammates)) as executor:
            futures = [
                executor.submit(run_peer_review, args, run_dir, teammate, review_teammates, edit_allowed)
                for teammate in review_teammates
            ]
            for future in as_completed(futures):
                name, returncode = future.result()
                print(f"Peer review {name} finished with return code {returncode}")
        append_messages(run_dir, "reviews")
        pause_for_questions(args, run_dir, "after peer review")
        run_project_manager_reports(args, run_dir, reporting, "after peer review", args.mode, edit_allowed)
    elif not args.skip_peer_review:
        print("Skipping peer review; fewer than two teammates completed the worker round successfully.")

    summary = run_dir / "summary.md"
    summary_log = run_dir / "logs" / "lead-summary.jsonl"
    if args.dry_run:
        summary.write_text(
            f"# Dry Run Summary\n\nWorkspace: {run_dir}\nTeammates: {len(teammates)}\n",
            encoding="utf-8",
        )
    else:
        rc = run_codex(args, synthesis_prompt(run_dir, args.mode, edit_allowed), summary, summary_log)
        if rc != 0:
            print("Lead synthesis failed; inspect outbox files manually.", file=sys.stderr)
            return rc

    print(f"Agent team summary: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
