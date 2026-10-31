import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.discover import load_state, save_state
from bot.followback import is_following_back, plan, run, unfollow_user

NOW = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)


def make_state(tmp, followed_at):
    state = {"seen": [], "followed": sorted(followed_at),
             "followed_at": dict(followed_at), "starred": [], "unfollowed": []}
    path = Path(tmp) / "state.json"
    save_state(path, state)
    return path


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.state = {
            "followed": ["old-noback", "old-back", "edge-noback", "young-noback", "legacy"],
            "followed_at": {
                "old-noback": "2026-09-01T00:00:00+00:00",    # 60 days
                "old-back": "2026-09-01T00:00:00+00:00",      # 60 days, follows back
                "edge-noback": "2026-09-30T10:00:00+00:00",   # exactly 31 days
                "young-noback": "2026-10-15T00:00:00+00:00",  # 16 days
                # legacy: no date recorded
            },
        }
        self.backs = {"old-back": True}

    def check(self, login):
        return self.backs.get(login, False)

    def test_due_kept_pending_untracked(self):
        result = plan(self.state, "me", now=NOW, check_fn=self.check)
        self.assertEqual([e["login"] for e in result["due"]],
                         ["old-noback", "edge-noback"])  # 31-day boundary counts
        self.assertEqual([e["login"] for e in result["kept"]], ["old-back"])
        self.assertEqual([e["login"] for e in result["pending"]], ["young-noback"])
        self.assertEqual(result["untracked"], ["legacy"])

    def test_custom_after_days(self):
        result = plan(self.state, "me", now=NOW, after_days=90, check_fn=self.check)
        self.assertEqual(result["due"], [])

    def test_plan_requires_checker(self):
        with self.assertRaises(ValueError):
            plan(self.state, "me", now=NOW)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.report = root / "reports" / "followback.md"
        self.state_path = make_state(self.tmp.name, {
            "old-noback": "2026-09-01T00:00:00+00:00",
            "old-back": "2026-09-01T00:00:00+00:00",
        })
        self.check = lambda login: login == "old-back"

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_never_unfollows_and_never_mutates_state(self):
        calls = []
        before = self.state_path.read_text(encoding="utf-8")
        result = run(self.state_path, self.report, self_login="me", now=NOW,
                     check_fn=self.check,
                     unfollow_fn=lambda login: calls.append(login) or True)
        self.assertEqual(calls, [])
        self.assertEqual(result["unfollowed"], [])
        self.assertEqual(self.state_path.read_text(encoding="utf-8"), before)
        self.assertIn("dry-run", self.report.read_text(encoding="utf-8"))

    def test_apply_unfollows_and_records_history(self):
        result = run(self.state_path, self.report, self_login="me", apply=True,
                     now=NOW, check_fn=self.check,
                     unfollow_fn=lambda login: True)
        self.assertEqual(result["unfollowed"], ["old-noback"])
        state = load_state(self.state_path)
        self.assertEqual(state["followed"], ["old-back"])
        self.assertNotIn("old-noback", state["followed_at"])
        self.assertEqual(state["unfollowed"][0]["login"], "old-noback")
        self.assertIn("no follow-back", state["unfollowed"][0]["reason"])

        calls = []
        result2 = run(self.state_path, self.report, self_login="me", apply=True,
                      now=NOW, check_fn=self.check,
                      unfollow_fn=lambda login: calls.append(login) or True)
        self.assertEqual(calls, [])  # second run is quiet
        self.assertEqual(result2["unfollowed"], [])

    def test_unfollow_cap_is_enforced(self):
        state_path = make_state(self.tmp.name, {
            f"user{i}": "2026-09-01T00:00:00+00:00" for i in range(4)})
        result = run(state_path, self.report, self_login="me", apply=True, now=NOW,
                     max_unfollow=2, check_fn=lambda login: False,
                     unfollow_fn=lambda login: True)
        self.assertEqual(len(result["unfollowed"]), 2)
        self.assertEqual(len(load_state(state_path)["followed"]), 2)

    def test_failed_unfollow_keeps_state(self):
        result = run(self.state_path, self.report, self_login="me", apply=True,
                     now=NOW, check_fn=self.check,
                     unfollow_fn=lambda login: False)
        self.assertEqual(result["unfollowed"], [])
        self.assertEqual(load_state(self.state_path)["followed"],
                         ["old-back", "old-noback"])

    def test_main_with_fixture(self):
        from bot.followback import main
        fixture = Path(self.tmp.name) / "backs.json"
        fixture.write_text(json.dumps({"old-back": True}), encoding="utf-8")
        out = str(Path(self.tmp.name) / "out.md")
        code = main(["--self", "me", "--fixture", str(fixture),
                     "--state", str(self.state_path), "--out", out])
        self.assertEqual(code, 0)
        text = Path(out).read_text(encoding="utf-8")
        self.assertIn("old-noback", text)


class HttpTests(unittest.TestCase):
    def fake_opener(self, status, sink):
        class Resp:
            def __init__(self): self.status = status
            def __enter__(self): return self
            def __exit__(self, *a): return False
        def opener(request):
            sink.append((request.get_method(), request.full_url))
            return Resp()
        return opener

    def test_is_following_back_uses_relationship_endpoint(self):
        calls = []
        self.assertTrue(is_following_back("alice", "me", "t",
                                          opener=self.fake_opener(204, calls)))
        self.assertEqual(calls[0][0], "GET")
        self.assertIn("/users/alice/following/me", calls[0][1])

    def test_unfollow_uses_delete(self):
        calls = []
        self.assertTrue(unfollow_user("alice", "t",
                                      opener=self.fake_opener(204, calls)))
        self.assertEqual(calls[0][0], "DELETE")
        self.assertIn("/user/following/alice", calls[0][1])


class DiscoverStateTests(unittest.TestCase):
    def test_followed_at_round_trip_and_unfollowed_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            save_state(path, {"seen": [], "followed": ["alice"],
                              "followed_at": {"alice": "2026-10-01T00:00:00+00:00"},
                              "starred": [],
                              "unfollowed": [{"login": "bob", "unfollowed_at": "x",
                                              "reason": "no follow-back after 31 days"}]})
            state = load_state(path)
            self.assertEqual(state["followed_at"]["alice"],
                             "2026-10-01T00:00:00+00:00")
            self.assertEqual(state["unfollowed"][0]["login"], "bob")


if __name__ == "__main__":
    unittest.main()
