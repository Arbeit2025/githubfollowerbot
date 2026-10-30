"""Profile the followers of a GitHub account: who are they?

Fetches the follower list and each follower's public profile from the
GitHub REST API, attaches simple rule-based tags (developer, student,
organization, bot-like, inactive, general) and renders a Markdown
report. The rules are transparent heuristics over public profile
fields only; they describe the account's public shape, not the person.

Usage:
  python -m bot.followers --user Arbeit2025 --out report.md
  python -m bot.followers --fixture examples/sample_profiles.json --out report.md

Auth: set GITHUB_TOKEN to raise the API rate limit. Without a token the
public limit (60 requests/hour) only covers very small follower lists.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from collections import Counter
from pathlib import Path

API = "https://api.github.com"

DEV_KEYWORDS = (
    "engineer", "developer", "programmer", "software", "coder",
    "エンジニア", "開発者", "プログラマ", "プログラマー",
)
STUDENT_KEYWORDS = (
    "student", "university", "college", "school",
    "学生", "大学", "学部", "院生",
)
BOT_KEYWORDS = ("bot", "automation", "ci", "自動")


def _request_json(url: str, token: str | None, opener=urllib.request.urlopen):
    request = urllib.request.Request(url)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", "githubfollowerbot")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with opener(request) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_follower_logins(user: str, token: str | None = None,
                          opener=urllib.request.urlopen,
                          per_page: int = 100, max_pages: int = 10) -> list[str]:
    """All follower logins for `user`, following pagination."""
    logins: list[str] = []
    for page in range(1, max_pages + 1):
        batch = _request_json(
            f"{API}/users/{user}/followers?per_page={per_page}&page={page}",
            token, opener)
        if not isinstance(batch, list):
            raise ValueError("followers response must be a list")
        logins.extend(str(item["login"]) for item in batch)
        if len(batch) < per_page:
            break
    return logins


def fetch_profile(login: str, token: str | None = None,
                  opener=urllib.request.urlopen) -> dict:
    profile = _request_json(f"{API}/users/{login}", token, opener)
    if not isinstance(profile, dict):
        raise ValueError("profile response must be an object")
    return profile


def classify(profile: dict) -> list[str]:
    """Rule-based tags for one public profile. Order is stable."""
    tags: list[str] = []
    login = str(profile.get("login") or "")
    bio = str(profile.get("bio") or "")
    text = f"{login} {bio}".lower()
    if profile.get("type") == "Organization":
        tags.append("organization")
    if login.endswith("[bot]") or any(k in text for k in BOT_KEYWORDS):
        tags.append("bot-like")
    if any(k.lower() in text for k in STUDENT_KEYWORDS):
        tags.append("student")
    repos = profile.get("public_repos") or 0
    if repos >= 5 or any(k.lower() in text for k in DEV_KEYWORDS):
        tags.append("developer")
    if repos == 0 and not bio and (profile.get("followers") or 0) == 0:
        tags.append("inactive")
    if not tags:
        tags.append("general")
    return tags


def summarize(profiles: list[dict]) -> dict:
    tag_counts: Counter = Counter()
    for profile in profiles:
        tag_counts.update(classify(profile))
    return {
        "total": len(profiles),
        "tag_counts": dict(tag_counts.most_common()),
        "organizations": sum(1 for p in profiles if p.get("type") == "Organization"),
    }


def render_report(user: str, profiles: list[dict]) -> str:
    summary = summarize(profiles)
    lines = [
        f"# GitHub followers report: @{user}",
        "",
        f"- Followers checked: {summary['total']}",
        f"- Organizations: {summary['organizations']}",
        "",
        "## Tags (rule-based, from public profile fields)",
        "",
    ]
    for tag, count in summary["tag_counts"].items():
        lines.append(f"- {tag}: {count}")
    lines += ["", "## Followers", ""]
    for profile in profiles:
        login = profile.get("login", "?")
        name = profile.get("name") or ""
        tags = ", ".join(classify(profile))
        lines.append(f"### [{login}](https://github.com/{login}) {name}".rstrip())
        lines.append("")
        lines.append(
            f"- tags: {tags}"
            f" / repos: {profile.get('public_repos') or 0}"
            f" / followers: {profile.get('followers') or 0}"
            f" / following: {profile.get('following') or 0}"
            f" / since: {str(profile.get('created_at') or '')[:10]}"
        )
        bio = str(profile.get("bio") or "").replace("\n", " ").strip()
        if bio:
            lines.append(f"- bio: {bio}")
        meta = [str(profile.get(k)) for k in ("company", "location") if profile.get(k)]
        if meta:
            lines.append(f"- {' / '.join(meta)}")
        lines.append("")
    lines.append("Tags are heuristics over public fields (bio keywords, "
                 "repo counts, account type), not a judgement of the person.")
    lines.append("")
    return "\n".join(lines)


def load_fixture(path: Path) -> tuple[str, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return str(data.get("user", "fixture")), list(data.get("profiles", []))
    if isinstance(data, list):
        return "fixture", list(data)
    raise ValueError("fixture must be an object or a list of profiles")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Profile a GitHub account's followers into a Markdown report")
    parser.add_argument("--user", default=None, help="GitHub username to inspect")
    parser.add_argument("--fixture", default=None,
                        help="offline JSON fixture instead of the API")
    parser.add_argument("--out", default="followers-report.md")
    parser.add_argument("--limit", type=int, default=0,
                        help="max followers to profile (0 = all)")
    args = parser.parse_args(argv)

    if args.fixture:
        user, profiles = load_fixture(Path(args.fixture))
    elif args.user:
        user = args.user
        token = os.environ.get("GITHUB_TOKEN") or None
        logins = fetch_follower_logins(user, token)
        if args.limit:
            logins = logins[: args.limit]
        profiles = [fetch_profile(login, token) for login in logins]
    else:
        parser.error("either --user or --fixture is required")

    report = render_report(user, profiles)
    Path(args.out).write_text(report, encoding="utf-8")
    print(f"wrote {args.out}: {len(profiles)} follower(s) of @{user}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
