"""
my_team_instructions.py — prints how to save the my-team JSON.

The transfer solver needs sell prices and the free-transfer count, which
are NOT public. This tool never sees, asks for, or stores an FPL
credential; you save the file yourself from your own logged-in browser.

Usage: .venv\\Scripts\\python.exe scripts/my_team_instructions.py
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from fpl.decide.optimiser import load_config  # noqa: E402
from fpl.decide import squad_state  # noqa: E402


def main() -> int:
    entry_id = load_config()["fpl"]["entry_id"]
    url = f"{squad_state.FPL_API}/my-team/{entry_id}/"
    dest = squad_state.MY_TEAM_PATH
    print(f"""
To refresh sell prices and your free-transfer count:

  1. Log in at https://fantasy.premierleague.com in your normal browser.
  2. Open this URL in that same browser:
         {url}
  3. Save the JSON it returns to:
         {dest}
     (Ctrl+S, or copy the text into that file.)

Why this way: sell price depends on what YOU paid, and neither it nor
your free-transfer count is in the public API. Step 2 works because YOUR
browser is already authenticated -- nothing in this repo has access to
that session, and no password, cookie or token is ever read or stored.

The file is gitignored. Re-save it whenever prices may have moved; the
solver refuses anything older than 24h, because a stale file produces a
confident, wrong, unactionable recommendation.
""".strip())
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"\n(Created {dest.parent} for you.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
