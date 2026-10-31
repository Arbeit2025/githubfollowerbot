"""followme-lite: discover, grade, and (only on request) follow/star repos.

A lightweight re-implementation of the *idea* behind yumiaura/followme
(discover GitHub repositories, grade them, follow promising authors and
star strong projects). No code is copied from that project (it carries
no licence); the pipeline shape and the default thresholds are the only
things carried over:

  fetch  -> GitHub Search for recently updated repositories
  grade  -> rule-based ``idea`` / ``skill`` scores, each in [1.0, 10.0],
            computed from public repository metadata only. No cloning,
            no local LLM (Ollama), so this runs anywhere the existing
            follower tools run, including GitHub Actions.
  act    -> follow authors whose best repo scores idea+skill > 14 and
            star repos scoring idea+skill > 16 (the original defaults),
            but ONLY with ``--apply``. The default is a dry run that
            just writes a candidate report.

Safety rails (this is the part that keeps it from becoming the kind of
mass-following automation the follower report itself flags as bot-like):

  * dry-run is the default; ``--apply`` needs a personal token in
    GITHUB_TOKEN with follow/star permission,
  * hard per-run caps (default: follow at most 5, star at most 10),
  * forks, archived repos, bot accounts and your own repos are excluded,
  * state (data/discover-state.json) remembers seen/followed/starred so
    nothing is acted on twice, and a dry run never mutates that state.

Usage:
  python -m bot.discover --fixture examples/sample_repos.json --out report.md
  python -m bot.discover --count 5 --language Python --out reports/discover.md
  GITHUB_TOKEN=... python -m bot.discover --apply --out reports/discover.md
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.github.com"

DEFAULT_FOLLOW_THRESHOLD = 14.0
DEFAULT_STAR_THRESHOLD = 16.0
DEFAULT_WINDOW_HOURS = 24
DEFAULT_MAX_FOLLOW = 5
DEFAULT_MAX_STAR = 10
DEFAULT_STATE = "data/discover-state.json"
DEFAULT_REPORT = "reports/discover.md"


# ---------------------------------------------------------------- HTTP

def _request_json(url: str, token: str | None, opener=urllib.request.urlopen):
    request = urllib.request.Request(url)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", "githubfollowerbot-discover")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with opener(request) as response:
        return json.loads(response.read().decode("utf-8"))


def _request_status(method: str, url: str, token: str | None,
                    opener=urllib.request.urlopen) -> int:
    """Status code for a no-body API call (follow/star and their checks)."""
    request = urllib.request.Request(url, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", "githubfollowerbot-discover")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with opener(request) as response:
            return int(getattr(response, "status", 0) or response.getcode())
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def fetch_self_login(token: str, opener=urllib.request.urlopen) -> str | None:
    try:
        data = _request_json(f"{API}/user", token, opener)
    except Exception:
        return None
    if isinstance(data, dict) and data.get("login"):
        return str(data["login"])
    return None


def is_following(login: str, token: str, opener=urllib.request.urlopen) -> bool:
    return _request_status("GET", f"{API}/user/following/{login}", token, opener) == 204


def follow_user(login: str, token: str, opener=urllib.request.urlopen) -> bool:
    """True when a new follow happened; False when already following/failed."""
    if is_following(login, token, opener):
        return False
    return _request_status("PUT", f"{API}/user/following/{login}", token, opener) == 204


def is_starred(full_name: str, token: str, opener=urllib.request.urlopen) -> bool:
    return _request_status("GET", f"{API}/user/starred/{full_name}", token, opener) == 204


def star_repo(full_name: str, token: str, opener=urllib.request.urlopen) -> bool:
    """True when a new star happened; False when already starred/failed."""
    if is_starred(full_name, token, opener):
        return False
    return _request_status("PUT", f"{API}/user/starred/{full_name}", token, opener) == 204


# ---------------------------------------------------------------- fetch

def normalize_repo(item: dict) -> dict:
    owner = item.get("owner") or {}
    license_info = item.get("license") or {}
    return {
        "full_name": str(item.get("full_name") or ""),
        "owner": str(owner.get("login") or ""),
        "owner_type": str(owner.get("type") or ""),
        "description": str(item.get("description") or ""),
        "topics": [str(t) for t in (item.get("topics") or [])],
        "language": str(item.get("language") or ""),
        "stargazers_count": int(item.get("stargazers_count") or 0),
        "forks_count": int(item.get("forks_count") or 0),
        "open_issues_count": int(item.get("open_issues_count") or 0),
        "size": int(item.get("size") or 0),
        "fork": bool(item.get("fork")),
        "archived": bool(item.get("archived")),
        "license": str(license_info.get("spdx_id") or "") if isinstance(license_info, dict) else "",
        "html_url": str(item.get("html_url") or ""),
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
        "pushed_at": str(item.get("pushed_at") or ""),
    }


def search_recent_repositories(token: str | None = None, language: str = "Python",
                               max_stars: int = 100, count: int = 5,
                               seen: set[str] | None = None,
                               opener=urllib.request.urlopen,
                               per_page: int = 100, max_pages: int = 10) -> list[dict]:
    """Recently updated repositories from GitHub Search, skipping `seen`."""
    seen = set(seen or ())
    found: list[dict] = []
    query = urllib.parse.urlencode({
        "q": f"language:{language} stars:<{max_stars}",
        "sort": "updated", "order": "desc", "per_page": per_page,
    })
    for page in range(1, max_pages + 1):
        data = _request_json(f"{API}/search/repositories?{query}&page={page}", token, opener)
        if not isinstance(data, dict):
            break
        items = data.get("items") or []
        if not items:
            break
        for item in items:
            if not isinstance(item, dict):
                continue
            repo = normalize_repo(item)
            if not repo["full_name"] or repo["full_name"] in seen:
                continue
            seen.add(repo["full_name"])
            found.append(repo)
            if len(found) >= count:
                return found
        if len(items) < per_page:
            break
    return found


def load_fixture(path: Path) -> tuple[str | None, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        repos = [normalize_repo(r) for r in data.get("repos", [])]
        return (str(data["user"]) if data.get("user") else None), repos
    if isinstance(data, list):
        return None, [normalize_repo(r) for r in data]
    raise ValueError("fixture must be an object or a list of repositories")


# ---------------------------------------------------------------- grading

def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


parse_ts = _parse_ts


def _clamp(value: float) -> float:
    return max(1.0, min(10.0, round(value, 2)))


def score_repo(repo: dict, now: datetime | None = None) -> dict:
    """Transparent rule-based idea/skill grades, each clamped to [1, 10]."""
    now = now or datetime.now(timezone.utc)
    idea, skill = 3.0, 3.0
    reasons: list[str] = []

    description = str(repo.get("description") or "").strip()
    if len(description) >= 50:
        idea += 2.0; reasons.append("clear, detailed description (+2.0 idea)")
    elif len(description) >= 15:
        idea += 1.0; reasons.append("description present (+1.0 idea)")

    topics = repo.get("topics") or []
    if topics:
        bonus = min(1.5, 0.5 * len(topics))
        idea += bonus; reasons.append(f"{len(topics)} topic(s) (+{bonus} idea)")

    stars = int(repo.get("stargazers_count") or 0)
    if stars >= 30:
        idea += 1.0; reasons.append(f"{stars} stars (+1.0 idea)")
    elif stars >= 5:
        idea += 0.5; reasons.append(f"{stars} stars (+0.5 idea)")

    forks = int(repo.get("forks_count") or 0)
    if forks >= 10:
        skill += 1.5; idea += 0.5; reasons.append(f"{forks} forks (+1.5 skill, +0.5 idea)")
    elif forks >= 3:
        skill += 1.0; reasons.append(f"{forks} forks (+1.0 skill)")
    elif forks >= 1:
        skill += 0.5; reasons.append(f"{forks} fork(s) (+0.5 skill)")

    pushed = _parse_ts(str(repo.get("pushed_at") or repo.get("updated_at") or ""))
    if pushed is not None:
        age_days = (now - pushed).total_seconds() / 86400
        if age_days <= 7:
            skill += 1.0; idea += 0.5; reasons.append("pushed within a week (+1.0 skill, +0.5 idea)")
        elif age_days <= 30:
            skill += 0.5; reasons.append("pushed within a month (+0.5 skill)")

    created = _parse_ts(str(repo.get("created_at") or ""))
    if created is not None:
        age_days = (now - created).total_seconds() / 86400
        if age_days >= 180:
            skill += 1.0; reasons.append("maintained for 6+ months (+1.0 skill)")
        elif age_days >= 30:
            skill += 0.5; reasons.append("older than a month (+0.5 skill)")

    size = int(repo.get("size") or 0)
    if size >= 1000:
        skill += 1.5; reasons.append("substantial codebase, 1MB+ (+1.5 skill)")
    elif size >= 200:
        skill += 1.0; reasons.append("mid-size codebase (+1.0 skill)")
    elif size >= 50:
        skill += 0.5; reasons.append("some code present (+0.5 skill)")

    if repo.get("license"):
        skill += 0.5; reasons.append(f"license: {repo['license']} (+0.5 skill)")
    if repo.get("language"):
        skill += 0.5; reasons.append("primary language detected (+0.5 skill)")

    idea, skill = _clamp(idea), _clamp(skill)
    return {"idea": idea, "skill": skill, "total": round(idea + skill, 2),
            "reasons": reasons}


def exclusion_reason(repo: dict, state: dict, self_login: str | None) -> str | None:
    owner = str(repo.get("owner") or "")
    if repo.get("fork"):
        return "fork"
    if repo.get("archived"):
        return "archived"
    if owner.endswith("[bot]") or str(repo.get("owner_type")) == "Bot":
        return "bot account"
    if self_login and owner.lower() == self_login.lower():
        return "own repository"
    if repo.get("full_name") in set(state.get("seen", [])):
        return "already seen"
    return None


def select_candidates(scored: list[dict], state: dict, self_login: str | None = None,
                      follow_threshold: float = DEFAULT_FOLLOW_THRESHOLD,
                      star_threshold: float = DEFAULT_STAR_THRESHOLD,
                      window_hours: int = DEFAULT_WINDOW_HOURS,
                      now: datetime | None = None,
                      max_follow: int = DEFAULT_MAX_FOLLOW,
                      max_star: int = DEFAULT_MAX_STAR) -> dict:
    """Split scored repos into follow/star candidate lists (strictly above
    the thresholds, inside the recency window, caps applied)."""
    now = now or datetime.now(timezone.utc)
    followed = set(state.get("followed", []))
    starred = set(state.get("starred", []))
    unfollowed_owners = {str(e.get("login") if isinstance(e, dict) else e)
                         for e in state.get("unfollowed", [])}
    follows: list[dict] = []
    stars: list[dict] = []
    seen_owners: set[str] = set()
    for entry in sorted(scored, key=lambda e: e["score"]["total"], reverse=True):
        repo, score = entry["repo"], entry["score"]
        if entry.get("excluded"):
            continue
        stamp = _parse_ts(str(repo.get("pushed_at") or repo.get("updated_at") or ""))
        if stamp is not None and (now - stamp).total_seconds() > window_hours * 3600:
            entry["excluded"] = f"outside {window_hours}h window"
            continue
        if score["total"] > star_threshold and repo["full_name"] not in starred:
            if len(stars) < max_star:
                stars.append(entry)
        if score["total"] > follow_threshold and repo["owner"] not in followed \
                and repo["owner"] not in unfollowed_owners \
                and repo["owner"] not in seen_owners:
            if len(follows) < max_follow:
                follows.append(entry)
                seen_owners.add(repo["owner"])
    return {"follow": follows, "star": stars}


# ---------------------------------------------------------------- state

def load_state(path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    followed = sorted(str(x) for x in data.get("followed", []) if isinstance(x, str))
    raw_followed_at = data.get("followed_at") or {}
    followed_at = {}
    if isinstance(raw_followed_at, dict):
        for login in followed:
            stamp = raw_followed_at.get(login)
            if isinstance(stamp, str) and stamp:
                followed_at[login] = stamp
    unfollowed = []
    for entry in data.get("unfollowed", []) or []:
        if isinstance(entry, dict) and entry.get("login"):
            unfollowed.append({"login": str(entry["login"]),
                               "unfollowed_at": str(entry.get("unfollowed_at") or ""),
                               "reason": str(entry.get("reason") or "")})
        elif isinstance(entry, str):
            unfollowed.append({"login": entry, "unfollowed_at": "", "reason": ""})
    return {
        "seen": sorted(str(x) for x in data.get("seen", []) if isinstance(x, str)),
        "followed": followed,
        "followed_at": followed_at,
        "starred": sorted(str(x) for x in data.get("starred", []) if isinstance(x, str)),
        "unfollowed": unfollowed,
        "updated_at": data.get("updated_at"),
    }


def save_state(path, state: dict) -> dict:
    followed = sorted(set(state.get("followed", [])))
    followed_at = {login: str(state.get("followed_at", {}).get(login))
                   for login in followed
                   if state.get("followed_at", {}).get(login)}
    payload = {
        "seen": sorted(set(state.get("seen", []))),
        "followed": followed,
        "followed_at": followed_at,
        "starred": sorted(set(state.get("starred", []))),
        "unfollowed": list(state.get("unfollowed", [])),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return payload


# ---------------------------------------------------------------- report

def render_report(scored: list[dict], candidates: dict, applied: bool,
                  follow_threshold: float, star_threshold: float,
                  window_hours: int) -> str:
    mode = "APPLIED (follow/star were executed)" if applied else "dry-run (no follow/star executed)"
    lines = [
        "# followme-lite candidates",
        "",
        f"- Mode: {mode}",
        f"- Follow threshold: idea+skill > {follow_threshold}",
        f"- Star threshold: idea+skill > {star_threshold}",
        f"- Window: pushed within {window_hours}h",
        f"- Repositories graded: {len(scored)}",
        f"- Follow candidates: {len(candidates['follow'])}",
        f"- Star candidates: {len(candidates['star'])}",
        "",
        "## Graded repositories",
        "",
    ]
    follow_names = {e["repo"]["full_name"] for e in candidates["follow"]}
    star_names = {e["repo"]["full_name"] for e in candidates["star"]}
    for entry in sorted(scored, key=lambda e: e["score"]["total"], reverse=True):
        repo, score = entry["repo"], entry["score"]
        marks = []
        if repo["full_name"] in star_names:
            marks.append("STAR candidate")
        if repo["full_name"] in follow_names:
            marks.append(f"FOLLOW candidate (@{repo['owner']})")
        if entry.get("excluded"):
            marks.append(f"excluded: {entry['excluded']}")
        lines.append(f"### [{repo['full_name']}]({repo['html_url']})".rstrip())
        lines.append("")
        lines.append(f"- idea: {score['idea']} / skill: {score['skill']} / total: {score['total']}")
        if marks:
            lines.append(f"- {' / '.join(marks)}")
        if score["reasons"]:
            lines.append(f"- why: {'; '.join(score['reasons'])}")
        if repo.get("description"):
            lines.append(f"- description: {repo['description']}")
        lines.append("")
    lines.append("Scores are transparent rules over public metadata "
                 "(description, topics, stars, forks, recency, size, license), "
                 "not an LLM judgement and not a judgement of the person.")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- run

def run(repos: list[dict], state_path=DEFAULT_STATE, report_path=DEFAULT_REPORT,
        apply: bool = False, self_login: str | None = None,
        follow_threshold: float = DEFAULT_FOLLOW_THRESHOLD,
        star_threshold: float = DEFAULT_STAR_THRESHOLD,
        window_hours: int = DEFAULT_WINDOW_HOURS,
        max_follow: int = DEFAULT_MAX_FOLLOW,
        max_star: int = DEFAULT_MAX_STAR,
        now: datetime | None = None,
        follow_fn=None, star_fn=None) -> dict:
    state = load_state(state_path)
    scored = []
    for repo in repos:
        entry = {"repo": repo, "score": score_repo(repo, now=now),
                 "excluded": exclusion_reason(repo, state, self_login)}
        scored.append(entry)
    candidates = select_candidates(
        scored, state, self_login, follow_threshold, star_threshold,
        window_hours, now, max_follow, max_star)

    followed_now: list[str] = []
    starred_now: list[str] = []
    if apply:
        if follow_fn is None or star_fn is None:
            raise ValueError("apply mode needs follow/star functions (a token)")
        for entry in candidates["follow"]:
            owner = entry["repo"]["owner"]
            follow_fn(owner)  # False means "already following": still sync state
            followed_now.append(owner)
        for entry in candidates["star"]:
            full_name = entry["repo"]["full_name"]
            star_fn(full_name)
            starred_now.append(full_name)
        state["seen"] = sorted(set(state["seen"]) | {e["repo"]["full_name"] for e in scored})
        state["followed"] = sorted(set(state["followed"]) | set(followed_now))
        stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        followed_at = dict(state.get("followed_at", {}))
        for login in followed_now:
            followed_at.setdefault(login, stamp)
        state["followed_at"] = followed_at
        state["starred"] = sorted(set(state["starred"]) | set(starred_now))
        save_state(state_path, state)

    target = Path(report_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_report(scored, candidates, apply, follow_threshold,
                                    star_threshold, window_hours), encoding="utf-8")
    return {"graded": len(scored), "follow_candidates": candidates["follow"],
            "star_candidates": candidates["star"], "applied": apply,
            "followed": followed_now, "starred": starred_now}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Discover, grade and (only with --apply) follow/star GitHub repos")
    parser.add_argument("--fixture", default=None, help="offline JSON fixture instead of the API")
    parser.add_argument("--count", type=int, default=5, help="repositories to fetch")
    parser.add_argument("--language", default="Python")
    parser.add_argument("--max-stars", type=int, default=100)
    parser.add_argument("--follow-threshold", type=float, default=DEFAULT_FOLLOW_THRESHOLD)
    parser.add_argument("--star-threshold", type=float, default=DEFAULT_STAR_THRESHOLD)
    parser.add_argument("--window-hours", type=int, default=DEFAULT_WINDOW_HOURS)
    parser.add_argument("--max-follow", type=int, default=DEFAULT_MAX_FOLLOW)
    parser.add_argument("--max-star", type=int, default=DEFAULT_MAX_STAR)
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--out", default=DEFAULT_REPORT)
    parser.add_argument("--apply", action="store_true",
                        help="actually follow/star (default is a dry run)")
    args = parser.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or None
    if args.apply and not token:
        parser.error("--apply requires GITHUB_TOKEN (a personal token with follow/star permission)")

    self_login = None
    if args.fixture:
        self_login, repos = load_fixture(Path(args.fixture))
    else:
        state = load_state(args.state)
        repos = search_recent_repositories(token, args.language, args.max_stars,
                                           args.count, seen=set(state["seen"]))
        if token:
            self_login = fetch_self_login(token)

    follow_fn = star_fn = None
    if args.apply:
        follow_fn = lambda login: follow_user(login, token)  # noqa: E731
        star_fn = lambda name: star_repo(name, token)  # noqa: E731

    result = run(repos, args.state, args.out, apply=args.apply, self_login=self_login,
                 follow_threshold=args.follow_threshold, star_threshold=args.star_threshold,
                 window_hours=args.window_hours, max_follow=args.max_follow,
                 max_star=args.max_star, follow_fn=follow_fn, star_fn=star_fn)
    mode = "applied" if args.apply else "dry-run"
    print(f"[{mode}] graded {result['graded']} repo(s); "
          f"follow candidates: {len(result['follow_candidates'])}; "
          f"star candidates: {len(result['star_candidates'])}; wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
