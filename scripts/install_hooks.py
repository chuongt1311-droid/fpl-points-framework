# scripts/install_hooks.py — one-time local setup, v4 plan §3.1.
#
# .git/hooks/ is never tracked by git, so the pre-commit secret scan
# (scripts/check_secrets.py) needs installing once per clone/machine. Run:
#   .venv\Scripts\python.exe scripts\install_hooks.py
import stat
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / ".git" / "hooks"
HOOK_PATH = HOOKS_DIR / "pre-commit"

HOOK_BODY = """#!/bin/sh
# Installed by scripts/install_hooks.py — do not edit by hand, edit
# scripts/check_secrets.py instead and re-run the installer.
python "$(git rev-parse --show-toplevel)/scripts/check_secrets.py"
exit $?
"""


def main():
    if not HOOKS_DIR.is_dir():
        print(f"error: {HOOKS_DIR} not found — is this a git repo?", file=sys.stderr)
        return 1
    if HOOK_PATH.exists() and "check_secrets.py" not in HOOK_PATH.read_text(errors="ignore"):
        print(
            f"error: {HOOK_PATH} already exists and isn't ours — "
            "merge scripts/check_secrets.py's call into it by hand.",
            file=sys.stderr,
        )
        return 1
    HOOK_PATH.write_text(HOOK_BODY)
    HOOK_PATH.chmod(HOOK_PATH.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"Installed pre-commit hook at {HOOK_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
