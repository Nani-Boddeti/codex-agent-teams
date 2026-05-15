#!/usr/bin/env python3
"""Install this repository's Agent Teams plugin into a local Codex home."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path


PLUGIN_NAME = "agent-teams"
MARKETPLACE_NAME = "agent-teams-marketplace"
PLUGIN_CONFIG_KEY = f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"
DEFAULT_ARCHIVE_URL = (
    "https://github.com/Nani-Boddeti/codex-agent-teams/archive/refs/heads/main.zip"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install Agent Teams for Codex.")
    parser.add_argument(
        "--codex-home",
        default=str(Path.home() / ".codex"),
        help="Codex home directory. Defaults to ~/.codex.",
    )
    parser.add_argument(
        "--agents-home",
        default=str(Path.home() / ".agents"),
        help="Agents metadata home. Defaults to ~/.agents.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing installed plugin directory.",
    )
    parser.add_argument(
        "--archive-url",
        default=DEFAULT_ARCHIVE_URL,
        help="Repository ZIP archive used when the script is run outside a checkout.",
    )
    return parser.parse_args()


def repo_root() -> Path | None:
    try:
        return Path(__file__).resolve().parents[1]
    except (NameError, IndexError):
        return None


def find_plugin_source(root: Path) -> Path | None:
    # Canonical location: .agents/plugins/plugins/<name>/
    agents_nested = root / ".agents" / "plugins" / "plugins" / PLUGIN_NAME
    if agents_nested.exists():
        return agents_nested
    # Legacy repo-root location
    nested = root / "plugins" / PLUGIN_NAME
    if nested.exists():
        return nested
    if (root / ".codex-plugin" / "plugin.json").exists():
        return root
    return None


def download_plugin_source(archive_url: str, temp_root: Path) -> Path:
    archive = temp_root / "agent-teams.zip"
    extract_root = temp_root / "source"
    urllib.request.urlretrieve(archive_url, archive)
    with zipfile.ZipFile(archive) as zip_file:
        zip_file.extractall(extract_root)

    plugin_source = find_plugin_source(extract_root)
    if plugin_source is not None:
        return plugin_source

    for candidate in extract_root.iterdir():
        if not candidate.is_dir():
            continue
        plugin_source = find_plugin_source(candidate)
        if plugin_source is not None:
            return plugin_source

    raise SystemExit(f"Downloaded archive did not contain plugins/{PLUGIN_NAME}.")


def copy_plugin(source: Path, target: Path, force: bool) -> None:
    if target.exists():
        if not force:
            raise SystemExit(f"{target} already exists. Re-run with --force to replace it.")
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def upsert_marketplace(agents_home: Path) -> Path:
    marketplace = agents_home / "plugins" / "marketplace.json"
    marketplace.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "name": PLUGIN_NAME,
        "source": {
            "source": "local",
            "path": f"./plugins/{PLUGIN_NAME}",
        },
        "policy": {
            "installation": "INSTALLED_BY_DEFAULT",
            "authentication": "ON_INSTALL",
        },
        "category": "Coding",
    }

    if marketplace.exists():
        data = json.loads(marketplace.read_text(encoding="utf-8"))
    else:
        data = {"plugins": []}

    data["name"] = MARKETPLACE_NAME
    interface = data.setdefault("interface", {})
    interface.setdefault("displayName", "Agent Teams Marketplace")

    plugins = data.setdefault("plugins", [])
    plugins[:] = [plugin for plugin in plugins if plugin.get("name") != PLUGIN_NAME]
    plugins.append(entry)
    marketplace.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return marketplace


def marketplace_config_block(marketplace_root: Path) -> str:
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    timestamp = timestamp.replace("+00:00", "Z")
    return "\n".join(
        [
            f"[marketplaces.{MARKETPLACE_NAME}]",
            f'last_updated = "{timestamp}"',
            'source_type = "local"',
            f"source = {json.dumps(str(marketplace_root))}",
            "",
        ]
    )


def plugin_config_block() -> str:
    return "\n".join(
        [
            f'[plugins."{PLUGIN_CONFIG_KEY}"]',
            "enabled = true",
            "",
        ]
    )


def upsert_config_section(config: Path, header: str, block: str) -> None:
    if not config.exists():
        config.write_text(block, encoding="utf-8")
        return

    lines = config.read_text(encoding="utf-8").splitlines(keepends=True)
    start = next((index for index, line in enumerate(lines) if line.strip() == header), None)
    block_lines = block.splitlines(keepends=True)

    if start is None:
        if lines and lines[-1].strip():
            lines.append("\n")
        lines.extend(block_lines)
    else:
        end = start + 1
        while end < len(lines):
            stripped = lines[end].strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                break
            end += 1
        lines[start:end] = block_lines

    config.write_text("".join(lines), encoding="utf-8")


def upsert_codex_marketplace(codex_home: Path, marketplace_root: Path) -> Path:
    config = codex_home / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    upsert_config_section(
        config,
        f"[marketplaces.{MARKETPLACE_NAME}]",
        marketplace_config_block(marketplace_root),
    )
    return config


def marketplace_plugin_target(agents_home: Path) -> Path:
    return agents_home.parent / "plugins" / PLUGIN_NAME


def enable_codex_plugin(codex_home: Path) -> Path:
    config = codex_home / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    upsert_config_section(config, f'[plugins."{PLUGIN_CONFIG_KEY}"]', plugin_config_block())
    return config


def main() -> int:
    args = parse_args()
    temp_download = None
    try:
        root = repo_root()
        plugin_source = find_plugin_source(root) if root is not None else None
        if plugin_source is None:
            temp_download = tempfile.TemporaryDirectory(prefix="agent-teams-source-")
            plugin_source = download_plugin_source(args.archive_url, Path(temp_download.name))

        codex_home = Path(args.codex_home).expanduser()
        agents_home = Path(args.agents_home).expanduser()
        codex_target = codex_home / "plugins" / PLUGIN_NAME
        marketplace_root = agents_home / "plugins"
        marketplace_target = marketplace_plugin_target(agents_home)

        copy_plugin(plugin_source, codex_target, args.force)
        copy_plugin(plugin_source, marketplace_target, args.force)
        marketplace = upsert_marketplace(agents_home)
        codex_config = upsert_codex_marketplace(codex_home, marketplace_root)
        enable_codex_plugin(codex_home)

        print(f"Installed plugin: {codex_target}")
        print(f"Installed marketplace plugin: {marketplace_target}")
        print(f"Updated marketplace: {marketplace}")
        print(f"Registered marketplace in Codex config: {codex_config}")
        print(f"Enabled plugin in Codex config: {PLUGIN_CONFIG_KEY}")
        print("Restart Codex if the plugin does not appear immediately.")
        return 0
    finally:
        if temp_download is not None:
            temp_download.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
