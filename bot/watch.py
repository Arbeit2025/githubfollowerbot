"""Run the follower report whenever the follower list changes.

GitHub Actions has no "new follower" event, so a scheduled workflow
polls the follower list and compares it with the state file kept in
the repository (data/followers-state.json). Only when the set of
followers actually changed does it regenerate the report
(reports/followers.md) and save the new state; the workflow then
commits both files. The first run (no state file yet) establishes
the baseline and always produces a report.

Usage:
  python -m bot.watch --user Arbeit2025
  python -m bot.watch --user Arbeit2025 --state data/followers-state.json \
      --out reports/followers.md

Auth: set GITHUB_TOKEN to raise the API rate limit.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from bot.followers import fetch_follower_logins, fetch_profile, render_report

DEFAULT_STATE = "data/followers-state.json"
DEFAULT_REPORT = "reports/followers.md"


def load_state(path) -> dict | None:
    """Stored state, or None when missing/unreadable (baseline run)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("logins"), list):
        return None
    return data


def save_state(path, user: str, logins: list[str]) -> dict:
    payload = {
        "user": user,
        "logins": sorted(logins),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return payload


def diff_logins(known: list[str], current: list[str]) -> tuple[list[str], list[str]]:
    """(new followers, removed followers), both sorted."""
    known_set, current_set = set(known), set(current)
    return sorted(current_set - known_set), sorted(known_set - current_set)


def run(user: str, state_path=DEFAULT_STATE, report_path=DEFAULT_REPORT,
        token: str | None = None, fetch_logins=None, fetch_profiles=None) -> dict:
    """Check once. Regenerates report+state only when the set changed."""
    if fetch_logins is None:
        fetch_logins = fetch_follower_logins
    if fetch_profiles is None:
        def fetch_profiles(logins):
            return [fetch_profile(login, token) for login in logins]

    current = [str(login) for login in fetch_logins(user, token)]
    state = load_state(state_path)
    baseline = state is None
    known = [] if baseline else [str(item) for item in state["logins"]]
    new, removed = diff_logins(known, current)
    changed = baseline or bool(new or removed)
    result = {"changed": changed, "baseline": baseline, "new": new,
              "removed": removed, "total": len(current)}
    if not changed:
        return result

    profiles = fetch_profiles(current)
    target = Path(report_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_report(user, profiles), encoding="utf-8")
    save_state(state_path, user, current)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate the followers report when the follower set changes")
    parser.add_argument("--user", required=True, help="GitHub username to watch")
    parser.add_argument("--state", default=DEFAULT_STATE,
                        help="state file path (kept in the repository)")
    parser.add_argument("--out", default=DEFAULT_REPORT, help="report output path")
    args = parser.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or None
    result = run(args.user, args.state, args.out, token)
    if result["baseline"]:
        print(f"baseline established for @{args.user}: "
              f"{result['total']} follower(s); report written to {args.out}")
    elif result["changed"]:
        print(f"follower change for @{args.user}: "
              f"+{len(result['new'])} {result['new']} "
              f"-{len(result['removed'])} {result['removed']}; "
              f"report updated at {args.out}")
    else:
        print(f"no follower change for @{args.user} ({result['total']} follower(s))")

    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"changed={'true' if result['changed'] else 'false'}\n")
            handle.write(f"new_count={len(result['new'])}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
