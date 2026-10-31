import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.discover import follows_today, load_fixture, load_state, run, save_state

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 2026-10-31 10:00 UTC = 19:00 JST, same calendar day in Tokyo.
NOW = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)


class DailyCapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.state = root / "state.json"
        self.report = root / "report.md"
        _user, self.repos = load_fixture(Path(ROOT) / "examples" / "sample_repos.json")

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, now=NOW, **kwargs):
        calls = {"follow": [], "star": []}
        result = run(self.repos, self.state, self.report, apply=True, now=now,
                     follow_fn=lambda login: calls["follow"].append(login) or True,
                     star_fn=lambda name: calls["star"].append(name) or True,
                     **kwargs)
        return result, calls

    def seed_log(self, entries):
        save_state(self.state, {"seen": [], "followed": [], "followed_at": {},
                                "follow_log": entries, "starred": [], "unfollowed": []})

    def test_follows_today_counts_tokyo_day(self):
        state = {"follow_log": [
            {"login": "a", "followed_at": "2026-10-31T14:30:00+00:00"},  # 23:30 JST, today
            {"login": "b", "followed_at": "2026-10-31T16:00:00+00:00"},  # 01:00 JST, Nov 1
            {"login": "c", "followed_at": "2026-10-30T14:59:00+00:00"},  # 23:59 JST, Oct 30
        ]}
        self.assertEqual(follows_today(state, NOW), 1)

    def test_daily_cap_limits_execution_and_logs(self):
        self.seed_log([
            {"login": "x", "followed_at": "2026-10-31T01:00:00+00:00"},
            {"login": "y", "followed_at": "2026-10-31T02:00:00+00:00"},
        ])
        result, calls = self.apply()
        # 2 follows already today + daily cap 3 -> only 1 of 2 candidates runs.
        self.assertEqual(len(calls["follow"]), 1)
        self.assertEqual(result["skipped_daily_cap"], 1)
        self.assertEqual(result["follows_today_before"], 2)
        state = load_state(self.state)
        self.assertEqual(len(state["follow_log"]), 3)
        self.assertEqual(follows_today(state, NOW), 3)

    def test_cap_reached_means_no_follows(self):
        self.seed_log([
            {"login": n, "followed_at": "2026-10-31T01:00:00+00:00"}
            for n in ("x", "y", "z")])
        result, calls = self.apply()
        self.assertEqual(calls["follow"], [])
        self.assertEqual(result["skipped_daily_cap"], 2)

    def test_next_day_allows_follows_again(self):
        self.seed_log([
            {"login": n, "followed_at": "2026-10-31T01:00:00+00:00"}
            for n in ("x", "y", "z")])
        result, calls = self.apply(now=NOW + timedelta(days=1),
                                   window_hours=24 * 7)
        self.assertEqual(len(calls["follow"]), 2)
        self.assertEqual(result["follows_today_before"], 0)

    def test_follow_log_is_recorded_on_apply(self):
        result, _calls = self.apply()
        state = load_state(self.state)
        logged = {e["login"] for e in state["follow_log"]}
        self.assertEqual(logged, {"alice", "bob"})
        self.assertEqual(result["skipped_daily_cap"], 0)

    def test_follow_only_never_stars(self):
        result, calls = self.apply(follow_only=True)
        self.assertEqual(calls["star"], [])
        self.assertEqual(result["starred"], [])
        self.assertEqual(sorted(result["followed"]), ["alice", "bob"])

    def test_zero_daily_cap_disables_limit(self):
        self.seed_log([
            {"login": n, "followed_at": "2026-10-31T01:00:00+00:00"}
            for n in ("x", "y", "z")])
        _result, calls = self.apply(max_follow_per_day=0)
        self.assertEqual(len(calls["follow"]), 2)


if __name__ == "__main__":
    unittest.main()
