"""Cross-platform, read-first environment checks for Obsidian Vault Assistant.

The module deliberately separates inspection and installation. Inspection only
looks at platform metadata and conventional application locations. Installation
is represented as an explicit command plan and requires confirmation before a
subprocess is ever started.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any


PENDING_SETUP_PLANS: dict[str, dict[str, Any]] = {}
PLAN_TTL_SECONDS = 30 * 60


def _existing(paths: list[Path]) -> list[str]:
    return [str(path) for path in paths if path.exists()]


def _platform_key() -> str:
    system = platform.system().casefold()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    if system == "linux":
        return "linux"
    return system or "unknown"


def _obsidian_locations() -> list[Path]:
    home = Path.home()
    key = _platform_key()
    if key == "macos":
        return [
            Path("/Applications/Obsidian.app"),
            home / "Applications" / "Obsidian.app",
        ]
    if key == "windows":
        program_files = os.environ.get("ProgramFiles", r"C:\\Program Files")
        local_app_data = os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local"))
        return [
            Path(program_files) / "Obsidian" / "Obsidian.exe",
            Path(local_app_data) / "Obsidian" / "Obsidian.exe",
        ]
    if key == "linux":
        return [Path("/usr/bin/obsidian"), Path("/usr/local/bin/obsidian"), home / ".local" / "bin" / "obsidian"]
    return []


def _vault_roots() -> list[str]:
    configured = os.environ.get("OBSIDIAN_VAULT_ROOT", "").strip()
    if configured:
        return [part for part in configured.split(os.pathsep) if part]
    home = Path.home()
    defaults = [home / "Documents" / "Obsidian", home / "Obsidian"]
    if _platform_key() == "macos":
        defaults.insert(0, home / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Obsidian")
    return [str(path) for path in defaults]


def check_environment() -> dict[str, Any]:
    """Return a safe snapshot without opening or changing any vault note."""
    locations = _obsidian_locations()
    found = _existing(locations)
    executable = shutil.which("obsidian")
    return {
        "platform": _platform_key(),
        "os": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "obsidian": {
            "installed": bool(found or executable),
            "known_locations": found,
            "path_command": executable,
        },
        "vault_roots": _vault_roots(),
        "cloud_sync": {
            "automated": False,
            "message": "Cloud sync is user-configured; this skill does not enable or upload to a cloud provider.",
        },
        "writes_performed": False,
        "next_step": "Call plan_environment_setup before any optional Obsidian installation or vault bootstrap.",
    }


def _installer_for(platform_key: str) -> dict[str, Any]:
    if platform_key == "macos":
        brew = shutil.which("brew")
        if brew:
            return {
                "method": "homebrew",
                "command": [brew, "install", "--cask", "obsidian"],
                "requires": "Homebrew",
            }
        return {
            "method": "manual-download",
            "url": "https://obsidian.md/download",
            "command": None,
            "requires": "A user-approved download and installer step",
        }
    if platform_key == "windows":
        winget = shutil.which("winget")
        if winget:
            return {
                "method": "winget",
                "command": [winget, "install", "--id", "Obsidian.Obsidian", "--exact", "--source", "winget"],
                "requires": "Windows Package Manager",
            }
        return {
            "method": "manual-download",
            "url": "https://obsidian.md/download",
            "command": None,
            "requires": "A user-approved download and installer step",
        }
    if platform_key == "linux":
        for name in ("flatpak", "snap"):
            binary = shutil.which(name)
            if binary and name == "flatpak":
                return {"method": "flatpak", "command": [binary, "install", "-y", "flathub", "md.obsidian.Obsidian"], "requires": "Flatpak"}
            if binary and name == "snap":
                return {"method": "snap", "command": [binary, "install", "obsidian", "--classic"], "requires": "Snap"}
        return {
            "method": "manual-download",
            "url": "https://obsidian.md/download",
            "command": None,
            "requires": "A user-approved download and installer step",
        }
    return {"method": "manual-download", "url": "https://obsidian.md/download", "command": None, "requires": "Manual installation"}


def plan_environment_setup() -> dict[str, Any]:
    snapshot = check_environment()
    installer = _installer_for(snapshot["platform"])
    if snapshot["obsidian"]["installed"]:
        return {
            "status": "installed",
            "environment": snapshot,
            "installation": None,
            "confirmation_required": False,
            "next_step": "Call list_vaults, get_vault_profile, and audit_vault_structure; use plan_vault_bootstrap for any missing folders.",
        }
    plan_id = uuid.uuid4().hex
    PENDING_SETUP_PLANS[plan_id] = {"created_at": time.time(), "snapshot": snapshot, "installation": installer}
    return {
        "plan_id": plan_id,
        "status": "not_installed",
        "environment": snapshot,
        "installation": installer,
        "confirmation_required": bool(installer.get("command")),
        "safety": "No installer is executed by this preview. A command plan must be shown to the user and explicitly approved.",
        "next_step": "After explicit approval, call apply_environment_setup with this plan_id and confirm=true, then repeat check_environment and vault discovery.",
    }


def apply_environment_setup(plan_id: str, confirm: bool) -> dict[str, Any]:
    """Run only the freshly generated, argument-array installer command."""
    if confirm is not True:
        raise ValueError("Installation requires confirm=true after the exact plan is approved.")
    if not plan_id:
        raise ValueError("plan_id is required; preview plan_environment_setup first.")
    pending = PENDING_SETUP_PLANS.get(plan_id)
    if not pending:
        raise ValueError("Unknown installation plan. Preview a fresh plan before applying it.")
    if time.time() - pending["created_at"] > PLAN_TTL_SECONDS:
        PENDING_SETUP_PLANS.pop(plan_id, None)
        raise ValueError("Installation plan expired. Generate a fresh plan.")
    current = check_environment()
    if current["obsidian"]["installed"]:
        PENDING_SETUP_PLANS.pop(plan_id, None)
        return {"status": "already_installed", "environment": current}
    installation = pending["installation"] or {}
    command = installation.get("command")
    if not command:
        PENDING_SETUP_PLANS.pop(plan_id, None)
        return {
            "status": "manual_required",
            "method": installation.get("method"),
            "url": installation.get("url"),
            "message": "No package manager was detected. Open the official download page and install Obsidian manually, then run check_environment again.",
        }
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=900, shell=False)
    PENDING_SETUP_PLANS.pop(plan_id, None)
    return {
        "status": "installed" if completed.returncode == 0 else "failed",
        "method": installation.get("method"),
        "returncode": completed.returncode,
        "stdout": completed.stdout[-2000:],
        "stderr": completed.stderr[-2000:],
        "writes_performed": False,
        "next_step": "Run check_environment again and then inspect vault structure." if completed.returncode == 0 else "Review the installer output and retry only after confirming the package manager state.",
    }
