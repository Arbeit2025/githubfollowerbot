import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.watch import diff_logins, load_state, run, save_state


def fake_profiles(logins):
    return [{"login": login, "name": None, "type": "User", "bio": "",
             "company": None, "location": None, "public_repos": 1,
             "followers": 0, "following": 0,
             "created_at": "2023-01-01T00:00:00Z"} for login in logins]


class DiffTests(unittest.TestCase):
    def test_new_and_removed(self):
        new, removed = diff_logins(["a", "b"], ["b", "c"])
        self.assertEqual(new, ["c"])
        self.assertEqual(removed, ["a"])

    def test_no_difference(self):
        self.assertEqual(diff_logins(["a"], ["a"]), ([], []))

    def test_everything_is_new_from_empty(self):
        new, removed = diff_logins([], ["x", "y"])
        self.assertEqual(new, ["x", "y"])
        self.assertEqual(removed, [])


class StateTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            save_state(path, "someone", ["b", "a"])
            state = load_state(path)
            self.assertEqual(state["user"], "someone")
            self.assertEqual(state["logins"], ["a", "b"])  # sorted on save

    def test_missing_or_broken_state_is_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_state(Path(tmp) / "nope.json"))
            broken = Path(tmp) / "broken.json"
            broken.write_text("{not json", encoding="utf-8")
            self.assertIsNone(load_state(broken))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.state = root / "data" / "state.json"
        self.report = root / "reports" / "followers.md"

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, logins):
        return run("watched", self.state, self.report,
                   fetch_logins=lambda user, token=None: list(logins),
                   fetch_profiles=fake_profiles)

    def test_baseline_creates_state_and_report(self):
        result = self.check(["alice", "bob"])
        self.assertTrue(result["changed"])
        self.assertTrue(result["baseline"])
        self.assertEqual(load_state(self.state)["logins"], ["alice", "bob"])
        self.assertIn("alice", self.report.read_text(encoding="utf-8"))

    def test_no_change_writes_nothing(self):
        self.check(["alice"])
        before = self.report.read_text(encoding="utf-8")
        result = self.check(["alice"])
        self.assertFalse(result["changed"])
        self.assertEqual(self.report.read_text(encoding="utf-8"), before)

    def test_new_follower_updates_state_and_report(self):
        self.check(["alice"])
        result = self.check(["alice", "carol"])
        self.assertTrue(result["changed"])
        self.assertEqual(result["new"], ["carol"])
        self.assertEqual(load_state(self.state)["logins"], ["alice", "carol"])
        self.assertIn("carol", self.report.read_text(encoding="utf-8"))

    def test_removal_also_counts_as_change(self):
        self.check(["alice", "bob"])
        result = self.check(["alice"])
        self.assertTrue(result["changed"])
        self.assertEqual(result["removed"], ["bob"])
        self.assertEqual(load_state(self.state)["logins"], ["alice"])


if __name__ == "__main__":
    unittest.main()
