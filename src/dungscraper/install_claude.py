"""Add DungScraper to Claude Desktop's config. Does not restart Claude."""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


def msix_claude_config(localappdata: Path) -> Path | None:
    """Store/MSIX Claude keeps its config under Packages\\Claude_*\\LocalCache.

    Returns that path when a Claude package folder exists. If several match,
    the one that already has a config file wins; otherwise the first name
    in sorted order is used.
    """
    packages = localappdata / "Packages"
    if not packages.is_dir():
        return None
    folders = sorted(path for path in packages.glob("Claude_*") if path.is_dir())
    if not folders:
        return None
    configs = [folder / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json" for folder in folders]
    for config in configs:
        if config.exists():
            return config
    return configs[0]


def claude_config_path(
    *,
    platform: str | None = None,
    home: Path | None = None,
    appdata: str | None = None,
    localappdata: str | None = None,
) -> Path:
    system = platform or sys.platform
    if system == "win32":
        local_root = Path(
            localappdata
            if localappdata is not None
            else os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        )
        msix = msix_claude_config(local_root)
        if msix is not None:
            return msix
        root = Path(appdata if appdata is not None else os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
        return root / "Claude" / "claude_desktop_config.json"
    base = home or Path.home()
    if system == "darwin":
        return base / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return base / ".config" / "Claude" / "claude_desktop_config.json"


def launch_command(launcher: str = "auto", *, from_spec: str | None = None) -> list[str]:
    """A stdio command that does not need Docker or a running API.

    `auto` uses the `dungscraper` already on PATH (pip, pipx, or a venv).
    `uvx` and `pipx` run the package without a prior install. Until the
    package is on an index, pass `from_spec` as a path or a git URL.
    """
    choice = (launcher or "auto").lower()
    spec = from_spec or "dungscraper"
    if choice == "uvx":
        return [shutil.which("uvx") or "uvx", "--from", spec, "dungscraper", "mcp"]
    if choice == "pipx":
        return [shutil.which("pipx") or "pipx", "run", "--spec", spec, "dungscraper", "mcp"]
    found = shutil.which("dungscraper")
    if choice == "auto" and found:
        return [found, "mcp"]
    return [sys.executable, "-m", "dungscraper", "mcp"]


def install_claude_config(
    config_path: Path,
    *,
    command: list[str],
    env: dict[str, str] | None = None,
) -> Path | None:
    """Merge the dungscraper MCP server into Claude's JSON config.

    An existing file is copied to `claude_desktop_config.json.bak-<timestamp>`
    before it is rewritten. Other MCP servers are left in place. Claude is
    not started or restarted; the user reopens Claude Desktop to load it.
    """
    if not command:
        raise ValueError("command must include the executable")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    existing: dict = {}
    if config_path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        backup = config_path.with_name(f"{config_path.name}.bak-{stamp}")
        backup.write_bytes(config_path.read_bytes())
        raw = config_path.read_text(encoding="utf-8").strip()
        if raw:
            loaded = json.loads(raw)
            if not isinstance(loaded, dict):
                raise ValueError(f"{config_path} is not a JSON object")
            existing = loaded
    servers = existing.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers["dungscraper"] = {"command": command[0], "args": command[1:], "env": env or {}}
    existing["mcpServers"] = servers
    config_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    return backup
