# scripts/check_secrets.py — v4 plan §3.1 (Tier 0.1).
#
# Pre-commit guard against re-leaking a token. The failure mode this exists
# for isn't "forgot to rotate" — it's "rotated, then leaked again" (the
# Bzzoiro token has already been flagged three times per docs/HANDOFF.md
# §9). Greps *staged* file contents for common secret-bearing patterns and
# refuses the commit if any are found.
#
# Usage:
#   .venv\Scripts\python.exe scripts\check_secrets.py       # check staged files
#   .venv\Scripts\python.exe scripts\check_secrets.py --all # check the whole tree (manual audit)
#
# Wired in as a git pre-commit hook (see scripts/install_hooks.py) since
# .git/hooks/ itself isn't tracked by git and needs a one-time local install.
import re
import subprocess
import sys

# Deliberately simple substring/regex patterns, not a full entropy scanner —
# this project has one known secret shape (a Bearer-token API key) and the
# goal is catching an accidental re-paste, not general-purpose secret
# scanning. Extend this list if a new source's auth shape shows up.
PATTERNS = [
    (re.compile(r"Token\s+[A-Za-z0-9_\-\.]{16,}"), "Token <...>"),
    (re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{16,}"), "Bearer <...>"),
    (re.compile(r"""api_key\s*[:=]\s*["']?[A-Za-z0-9_\-]{16,}""", re.I), "api_key = <...>"),
    (re.compile(r"""bzzoiro[_-]?(token|key)\s*[:=]\s*["']?[A-Za-z0-9_\-]{8,}""", re.I), "bzzoiro token/key"),
]

# Never flag ourselves, and never flag .env.example-style placeholders.
ALLOW_FILENAMES = {"check_secrets.py", ".env.example"}


def staged_files():
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True, check=True,
    )
    return [f for f in out.stdout.splitlines() if f.strip()]


def all_tracked_files():
    out = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True,
    )
    return [f for f in out.stdout.splitlines() if f.strip()]


def scan(paths):
    findings = []
    for path in paths:
        import os
        if os.path.basename(path) in ALLOW_FILENAMES:
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except (FileNotFoundError, IsADirectoryError, PermissionError):
            continue
        for pattern, label in PATTERNS:
            for m in pattern.finditer(text):
                line_no = text.count("\n", 0, m.start()) + 1
                findings.append((path, line_no, label))
    return findings


def main():
    check_all = "--all" in sys.argv
    paths = all_tracked_files() if check_all else staged_files()
    findings = scan(paths)
    if findings:
        print("check_secrets.py: possible secret(s) found — commit blocked:\n")
        for path, line_no, label in findings:
            print(f"  {path}:{line_no}  matched pattern: {label}")
        print(
            "\nIf this is a real secret: remove it, put it in .env (gitignored), "
            "and rotate it on the issuing service if it was ever committed before.\n"
            "If this is a false positive, tighten PATTERNS in scripts/check_secrets.py "
            "rather than committing around the check."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
