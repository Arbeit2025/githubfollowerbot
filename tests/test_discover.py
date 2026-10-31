import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.discover import (load_fixture, load_state, normalize_repo, run,
                          score_repo, search_recent_repositories,
                          select_candidates, _request_status)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)


def raw_repo(**overrides):
    base = {
        "full_name": "alice/strong-tool",
        "owner": {"login": "alice", "type": "User"},
        "description": "A carefully built command line tool that converts "
                       "subtitle files between many formats reliably.",
        "topics": ["cli", "subtitles", "converter"],
        "language": "Python",
        "stargazers_count": 40,
        "forks_count": 12,
        "open_issues_count": 2,
        "size": 2500,
        "fork": False,
        "archived": False,
        "license": {"spdx_id": "MIT"},
        "html_url": "https://github.com/alice/strong-tool",
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2026-10-30T23:00:00Z",
        "pushed_at": "2026-10-30T23:00:00Z",
    }
    base.update(overrides)
    return base


def scored_entry(repo, total_owner=None):
    normalized = normalize_repo(repo)
    return {"repo": normalized, "score": score_repo(normalized, now=NOW),
            "excluded": None}


class ScoreTests(unittest.TestCase):
    def setUp(self):
        _user, self.repos = load_fixture(Path(ROOT) / "examples" / "sample_repos.json")
        self.by_name = {r["full_name"]: r for r in self.repos}

    def test_strong_repo_scores(self):
        score = score_repo(self.by_name["alice/strong-tool"], now=NOW)
        self.assertEqual(score["idea"], 8.5)
        self.assertEqual(score["skill"], 9.0)
        self.assertEqual(score["total"], 17.5)

    def test_mid_repo_scores(self):
        score = score_repo(self.by_name["bob/mid-tool"], now=NOW)
        self.assertEqual(score["total"], 14.5)

    def test_weak_repo_scores_low(self):
        score = score_repo(self.by_name["carol/tiny-script"], now=NOW)
        self.assertLess(score["total"], 14.0)

    def test_scores_are_clamped(self):
        repo = normalize_repo(raw_repo(stargazers_count=9999, forks_count=9999,
                                       size=999999, topics=["a"] * 20))
        score = score_repo(repo, now=NOW)
        self.assertLessEqual(score["idea"], 10.0)
        self.assertLessEqual(score["skill"], 10.0)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        _user, self.repos = load_fixture(Path(ROOT) / "examples" / "sample_repos.json")

    def select(self, state=None, **kwargs):
        from bot.discover import exclusion_reason
        state = state or {"seen": [], "followed": [], "starred": []}
        entries = []
        for repo in self.repos:
            entries.append({"repo": repo, "score": score_repo(repo, now=NOW),
                            "excluded": exclusion_reason(repo, state, "sample-owner")})
        return select_candidates(entries, state, "sample-owner", now=NOW, **kwargs), entries

    def test_thresholds_are_strict_and_split(self):
        candidates, _entries = self.select()
        self.assertEqual([e["repo"]["full_name"] for e in candidates["star"]],
                         ["alice/strong-tool"])
        self.assertEqual([e["repo"]["full_name"] for e in candidates["follow"]],
                         ["alice/strong-tool", "bob/mid-tool"])

    def test_fork_is_excluded(self):
        _candidates, entries = self.select()
        forked = [e for e in entries if e["repo"]["full_name"] == "dave/forked-tool"][0]
        self.assertEqual(forked["excluded"], "fork")

    def test_window_excludes_old_repos(self):
        candidates, _entries = self.select(window_hours=1)
        self.assertEqual(candidates["follow"], [])
        self.assertEqual(candidates["star"], [])

    def test_caps_are_enforced(self):
        entries = []
        for i in range(8):
            repo = normalize_repo(raw_repo(full_name=f"user{i}/tool{i}",
                                           owner={"login": f"user{i}", "type": "User"}))
            entries.append({"repo": repo, "score": score_repo(repo, now=NOW),
                            "excluded": None})
        state = {"seen": [], "followed": [], "starred": []}
        candidates = select_candidates(entries, state, None, now=NOW,
                                       max_follow=5, max_star=3)
        self.assertEqual(len(candidates["follow"]), 5)
        self.assertEqual(len(candidates["star"]), 3)

    def test_already_followed_and_starred_are_skipped(self):
        state = {"seen": [], "followed": ["alice"], "starred": ["alice/strong-tool"]}
        candidates, _entries = self.select(state=state)
        self.assertEqual(candidates["star"], [])
        self.assertEqual([e["repo"]["owner"] for e in candidates["follow"]], ["bob"])


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.state = root / "data" / "state.json"
        self.report = root / "reports" / "discover.md"
        _user, self.repos = load_fixture(Path(ROOT) / "examples" / "sample_repos.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_never_acts_and_never_mutates_state(self):
        calls = []
        result = run(self.repos, self.state, self.report, now=NOW,
                     follow_fn=lambda login: calls.append(("follow", login)),
                     star_fn=lambda name: calls.append(("star", name)))
        self.assertFalse(result["applied"])
        self.assertEqual(calls, [])
        self.assertFalse(self.state.exists())
        text = self.report.read_text(encoding="utf-8")
        self.assertIn("dry-run", text)
        self.assertIn("alice/strong-tool", text)

    def test_apply_acts_updates_state_and_second_run_is_quiet(self):
        calls = []
        result = run(self.repos, self.state, self.report, apply=True, now=NOW,
                     follow_fn=lambda login: calls.append(("follow", login)) or True,
                     star_fn=lambda name: calls.append(("star", name)) or True)
        self.assertTrue(result["applied"])
        self.assertEqual(sorted(result["followed"]), ["alice", "bob"])
        self.assertEqual(result["starred"], ["alice/strong-tool"])
        state = load_state(self.state)
        self.assertEqual(state["followed"], ["alice", "bob"])
        self.assertEqual(state["starred"], ["alice/strong-tool"])
        self.assertIn("dave/forked-tool", state["seen"])

        calls.clear()
        result2 = run(self.repos, self.state, self.report, apply=True, now=NOW,
                      follow_fn=lambda login: calls.append(("follow", login)) or True,
                      star_fn=lambda name: calls.append(("star", name)) or True)
        self.assertEqual(calls, [])
        self.assertEqual(result2["followed"], [])

    def test_main_with_fixture_writes_report(self):
        from bot.discover import main
        out = str(Path(self.tmp.name) / "out.md")
        state = str(Path(self.tmp.name) / "state.json")
        code = main(["--fixture", os.path.join(ROOT, "examples", "sample_repos.json"),
                     "--out", out, "--state", state])
        self.assertEqual(code, 0)
        self.assertIn("followme-lite candidates", Path(out).read_text(encoding="utf-8"))


class HttpTests(unittest.TestCase):
    def test_request_status_reads_204(self):
        class Resp:
            status = 204
            def __enter__(self): return self
            def __exit__(self, *a): return False
        self.assertEqual(_request_status("PUT", "https://example.invalid/x", "t",
                                         opener=lambda req: Resp()), 204)

    def test_search_skips_seen_and_normalizes(self):
        payload = {"items": [raw_repo(), raw_repo(full_name="alice/other",
                                                  owner={"login": "alice", "type": "User"})]}
        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps(payload).encode()
        repos = search_recent_repositories("t", count=5, seen={"alice/strong-tool"},
                                           opener=lambda req: Resp(), per_page=2)
        self.assertEqual([r["full_name"] for r in repos], ["alice/other"])
        self.assertEqual(repos[0]["owner"], "alice")


if __name__ == "__main__":
    unittest.main()
