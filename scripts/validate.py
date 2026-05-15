#!/usr/bin/env python3
"""Validate repository structure for the Agent Teams Codex plugin."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "agent-teams"


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"Invalid JSON at {path}: {exc}") from exc


def require(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"Missing required path: {path}")


def validate_installer_registration() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-teams-install-") as tmp:
        tmp_root = Path(tmp)
        codex_home = tmp_root / "codex"
        agents_home = tmp_root / "agents"
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "install_local.py"),
                "--codex-home",
                str(codex_home),
                "--agents-home",
                str(agents_home),
                "--force",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        require(codex_home / "plugins" / "agent-teams" / "skills" / "team" / "SKILL.md")
        require(agents_home / "plugins" / "plugins" / "agent-teams" / "skills" / "team" / "SKILL.md")
        require(agents_home / "plugins" / "marketplace.json")
        require(codex_home / "config.toml")

        marketplace = load_json(agents_home / "plugins" / "marketplace.json")
        if marketplace.get("name") != "agent-teams-marketplace":
            raise SystemExit("installer marketplace name must match Codex config key")

        config = (codex_home / "config.toml").read_text(encoding="utf-8")
        expected_source = f"source = {json.dumps(str(agents_home / 'plugins'))}"
        if "[marketplaces.agent-teams-marketplace]" not in config:
            raise SystemExit("installer must register agent-teams marketplace in Codex config")
        if expected_source not in config:
            raise SystemExit("installer Codex config must point to the local marketplace root")
        if '[plugins."agent-teams@agent-teams-marketplace"]' not in config:
            raise SystemExit("installer must enable the agent-teams plugin in Codex config")
        if "enabled = true" not in config:
            raise SystemExit("installer must mark the agent-teams plugin enabled")


def main() -> int:
    require(PLUGIN_ROOT / ".codex-plugin" / "plugin.json")
    require(PLUGIN_ROOT / "skills" / "team" / "SKILL.md")
    require(PLUGIN_ROOT / "scripts" / "agent_team.py")

    manifest = load_json(PLUGIN_ROOT / ".codex-plugin" / "plugin.json")
    if manifest.get("name") != "agent-teams":
        raise SystemExit("plugin.json name must be agent-teams")

    marketplace = ROOT / ".agents" / "plugins" / "marketplace.json"
    if marketplace.exists():
        data = load_json(marketplace)
        entries = data.get("plugins", [])
        agent_teams_entry = next(
            (entry for entry in entries if entry.get("name") == "agent-teams"),
            None,
        )
        if agent_teams_entry is None:
            raise SystemExit("marketplace.json must include agent-teams")
        source = agent_teams_entry.get("source", {})
        if source.get("source") != "local":
            raise SystemExit("agent-teams marketplace source must be local")
        if source.get("path") != "./plugins/agent-teams":
            raise SystemExit("agent-teams marketplace path must be ./plugins/agent-teams")
        policy = agent_teams_entry.get("policy", {})
        if policy.get("installation") != "INSTALLED_BY_DEFAULT":
            raise SystemExit("agent-teams policy must be INSTALLED_BY_DEFAULT")

    for text_path in [
        ROOT / "README.md",
        PLUGIN_ROOT / "skills" / "team" / "SKILL.md",
    ]:
        text = text_path.read_text(encoding="utf-8").lower()
        for blocked in ["family medical", "medical history", "clinical intake"]:
            if blocked in text:
                raise SystemExit(f"Remove specific example '{blocked}' from {text_path}")

    subprocess.run(
        [sys.executable, "-m", "py_compile", str(PLUGIN_ROOT / "scripts" / "agent_team.py")],
        check=True,
    )
    subprocess.run(
        [sys.executable, "-m", "py_compile", str(ROOT / "scripts" / "install_local.py")],
        check=True,
    )
    validate_installer_registration()
    print("Agent Teams plugin validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
