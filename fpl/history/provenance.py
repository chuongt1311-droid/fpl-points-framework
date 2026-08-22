"""
provenance.py — run metadata for the archive (spec §3.6).

Without config_sha256 you cannot tell a real projection revision from a
config.yaml parameter you changed on a Wednesday — which is the whole
question the archive exists to answer.

Nothing here guesses. A field that isn't genuinely knowable is None.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fpl.history import paths

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config.yaml"


def config_sha256() -> Optional[str]:
    try:
        return hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest()
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return None


def git_state() -> tuple[Optional[str], Optional[bool]]:
    """(sha, dirty). Both None if git isn't available or this isn't a repo —
    an honest unknown beats a fabricated SHA."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True, timeout=15,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True, timeout=15,
        ).stdout
        return sha, bool(status.strip())
    except (subprocess.SubprocessError, OSError):
        return None, None


def _hours_to_deadline(now: datetime, deadline_utc: Optional[str]) -> Optional[float]:
    if not deadline_utc:
        return None
    try:
        deadline = datetime.fromisoformat(deadline_utc.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (deadline - now).total_seconds() / 3600.0


def build_run_metadata(
    target_gameweek: Optional[int],
    deadline_utc: Optional[str],
    now: Optional[datetime] = None,
) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    sha, dirty = git_state()
    return {
        "run_id": str(uuid.uuid4()),
        "asof": paths.format_asof(now),
        "asof_iso": now.isoformat(),
        "git_sha": sha,
        "git_dirty": dirty,
        "config_sha256": config_sha256(),
        "trigger": os.environ.get("GITHUB_EVENT_NAME", "local"),
        "target_gameweek": target_gameweek,
        "deadline_utc": deadline_utc,
        "hours_to_deadline": _hours_to_deadline(now, deadline_utc),
        "provenance": "recorded",
    }
