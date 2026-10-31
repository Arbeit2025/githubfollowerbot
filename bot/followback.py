"""Follow-back check: unfollow accounts that never followed back.

Companion to bot.discover (followme-lite). Discover's ``--apply`` mode
records *when* it followed someone in the shared state file
(data/discover-state.json, keys ``followed`` + ``followed_at``). This
tool looks only at those tool-made follows — accounts the user followed
by hand are never touched — and, once a follow is at least
``--after-days`` old (default 31, i.e. about a month), checks whether
that account follows the user back. Accounts that do not are unfollow
candidates.

Safety rails, same style as the rest of this repository:

  * dry-run is the default; ``--apply`` is required to actually
    unfollow, and needs a personal token in GITHUB_TOKEN,
  * a per-run cap (default 5 unfollows),
  * follows without a recorded date (state written before this feature
    existed) are listed as untracked and are NEVER unfollowed, because
    their age cannot be proven,
  * an unfollowed account is written to the state's ``unfollowed``
    history, and discover will not follow it again,
  * checking uses the public relationship endpoint only; nobody is
    contacted and nothing is posted.

Usage:
  python -m bot.followback --self Arbeit2025
  GITHUB_TOKEN=... python -m bot.followback --self Arbeit2025 --apply
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from bot.discover import (API, _request_status, load_state, parse_ts,
                          save_state)

DEFAULT_AFTER_DAYS = 31
DEFAULT_MAX_UNFOLLOW = 5
DEFAULT_STATE = "data/discover-state.json"
DEFAULT_REPORT = "reports/followback.md"


# ---------------------------------------------------------------- GitHub

def is_following_back(login: str, self_login: str, token: str | None = None,
                      opener=None) -> bool:
    """True when `login` follows `self_login` (public relationship check)."""
    import urllib.request
    return _request_status(
        "GET", f"{API}/users/{login}/following/{self_login}", token,
        opener or urllib.request.urlopen) == 204


def unfollow_user(login: str, token: str, opener=None) -> bool:
    """DELETE the follow. True on 204 (unfollowed or already not following)."""
    import urllib.request
    return _request_status(
        "DELETE", f"{API}/user/following/{login}", token,
        opener or urllib.request.urlopen) == 204


# ---------------------------------------------------------------- planning

def plan(state: dict, self_login: str, now: datetime | None = None,
         after_days: int = DEFAULT_AFTER_DAYS, check_fn=None) -> dict:
    """Who is due for unfollowing, who followed back, who is untracked.

    check_fn(login) -> bool (follows back). Required: planning without a
    follow-back check would unfollow blindly, so there is no default.
    """
    if check_fn is None:
        raise ValueError("plan() needs a follow-back checker")
    now = now or datetime.now(timezone.utc)
    followed_at = state.get("followed_at", {})
    due, kept, untracked, pending = [], [], [], []
    for login in state.get("followed", []):
        stamp = parse_ts(str(followed_at.get(login) or ""))
        if stamp is None:
            untracked.append(login)
            continue
        age_days = (now - stamp).total_seconds() / 86400
        entry = {"login": login, "followed_at": followed_at[login],
                 "age_days": round(age_days, 1)}
        if age_days < after_days:
            pending.append(entry)
            continue
        if check_fn(login):
            kept.append(entry)
        else:
            due.append(entry)
    return {"due": due, "kept": kept, "pending": pending, "untracked": untracked}


# ---------------------------------------------------------------- report

def render_report(result: dict, self_login: str, after_days: int,
                  applied: bool) -> str:
    mode = "APPLIED (unfollows were executed)" if applied else "dry-run (no unfollow executed)"
    lines = [
        "# followback check",
        "",
        f"- Account: @{self_login}",
        f"- Mode: {mode}",
        f"- Rule: unfollow when a tool-made follow is at least {after_days} days old "
        "and the account does not follow back",
        f"- Tracked follows: {result['tracked']}",
        f"- Due (no follow-back): {len(result['plan']['due'])}",
        f"- Unfollowed this run: {len(result['unfollowed'])}",
        "",
    ]
    if result["plan"]["due"]:
        lines += ["## No follow-back (due)", ""]
        for entry in result["plan"]["due"]:
            mark = "unfollowed" if entry["login"] in result["unfollowed"] else "candidate"
            lines.append(f"- @{entry['login']} — followed {entry['followed_at'][:10]} "
                         f"({entry['age_days']} days ago) — {mark}")
        lines.append("")
    if result["plan"]["kept"]:
        lines += ["## Followed back (kept)", ""]
        for entry in result["plan"]["kept"]:
            lines.append(f"- @{entry['login']} — followed {entry['followed_at'][:10]}")
        lines.append("")
    if result["plan"]["pending"]:
        lines += ["## Not yet due", ""]
        for entry in result["plan"]["pending"]:
            lines.append(f"- @{entry['login']} — followed {entry['followed_at'][:10]} "
                         f"({entry['age_days']} days ago)")
        lines.append("")
    if result["plan"]["untracked"]:
        lines += ["## Untracked (no follow date recorded; never auto-unfollowed)", ""]
        for login in result["plan"]["untracked"]:
            lines.append(f"- @{login}")
        lines.append("")
    lines.append("Only follows made by bot.discover --apply are tracked here; "
                 "accounts followed by hand are never listed or unfollowed.")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- run

def run(state_path=DEFAULT_STATE, report_path=DEFAULT_REPORT,
        self_login: str = "", apply: bool = False,
        after_days: int = DEFAULT_AFTER_DAYS,
        max_unfollow: int = DEFAULT_MAX_UNFOLLOW,
        now: datetime | None = None, check_fn=None, unfollow_fn=None) -> dict:
    state = load_state(state_path)
    planned = plan(state, self_login, now=now, after_days=after_days,
                   check_fn=check_fn)
    unfollowed_now: list[str] = []
    if apply:
        if unfollow_fn is None:
            raise ValueError("apply mode needs an unfollow function (a token)")
        for entry in planned["due"][: max(0, max_unfollow)]:
            login = entry["login"]
            if unfollow_fn(login):
                unfollowed_now.append(login)
        if unfollowed_now:
            gone = set(unfollowed_now)
            state["followed"] = [l for l in state["followed"] if l not in gone]
            state["followed_at"] = {l: s for l, s in state.get("followed_at", {}).items()
                                     if l not in gone}
            stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
            history = list(state.get("unfollowed", []))
            history += [{"login": login, "unfollowed_at": stamp,
                         "reason": f"no follow-back after {after_days} days"}
                        for login in unfollowed_now]
            state["unfollowed"] = history
            save_state(state_path, state)

    result = {"tracked": len(state.get("followed", [])) + len(unfollowed_now),
              "plan": planned, "unfollowed": unfollowed_now, "applied": apply}
    target = Path(report_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_report(result, self_login, after_days, apply),
                      encoding="utf-8")
    return result


def load_fixture(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("fixture must be an object like {\"alice\": true}")
    return {str(k): bool(v) for k, v in data.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Unfollow tool-made follows that never followed back")
    parser.add_argument("--self", required=True, dest="self_login",
                        help="your own GitHub username")
    parser.add_argument("--after-days", type=int, default=DEFAULT_AFTER_DAYS,
                        help="days without a follow-back before unfollowing")
    parser.add_argument("--max-unfollow", type=int, default=DEFAULT_MAX_UNFOLLOW)
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--out", default=DEFAULT_REPORT)
    parser.add_argument("--fixture", default=None,
                        help="offline JSON map of login -> follows-back (bool)")
    parser.add_argument("--apply", action="store_true",
                        help="actually unfollow (default is a dry run)")
    args = parser.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or None
    if args.apply and not token and not args.fixture:
        parser.error("--apply requires GITHUB_TOKEN (a personal token with follow permission)")

    if args.fixture:
        backs = load_fixture(Path(args.fixture))
        check_fn = lambda login: backs.get(login, False)  # noqa: E731
        unfollow_fn = lambda login: True  # noqa: E731
    else:
        check_fn = lambda login: is_following_back(login, args.self_login, token)  # noqa: E731
        unfollow_fn = lambda login: unfollow_user(login, token)  # noqa: E731

    result = run(args.state, args.out, self_login=args.self_login, apply=args.apply,
                 after_days=args.after_days, max_unfollow=args.max_unfollow,
                 check_fn=check_fn, unfollow_fn=unfollow_fn)
    mode = "applied" if args.apply else "dry-run"
    print(f"[{mode}] tracked follows: {result['tracked']}; "
          f"due: {len(result['plan']['due'])}; "
          f"unfollowed: {len(result['unfollowed'])}; wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
