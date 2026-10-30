import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.followers import classify, load_fixture, render_report, summarize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def profile(**overrides):
    base = {
        "login": "someone",
        "name": "Some One",
        "type": "User",
        "bio": "",
        "company": None,
        "location": None,
        "public_repos": 1,
        "followers": 2,
        "following": 2,
        "created_at": "2023-01-01T00:00:00Z",
    }
    base.update(overrides)
    return base


class ClassifyTests(unittest.TestCase):
    def test_developer_by_bio_keyword(self):
        self.assertIn("developer", classify(profile(bio="Software engineer")))

    def test_developer_by_repo_count(self):
        self.assertIn("developer", classify(profile(public_repos=5)))

    def test_student_by_japanese_keyword(self):
        self.assertIn("student", classify(profile(bio="大学で勉強中です")))

    def test_organization(self):
        self.assertEqual(classify(profile(type="Organization"))[0], "organization")

    def test_bot_like_by_suffix(self):
        self.assertIn("bot-like", classify(profile(login="dependabot[bot]")))

    def test_inactive(self):
        tags = classify(profile(public_repos=0, followers=0, bio=""))
        self.assertIn("inactive", tags)

    def test_general_fallback(self):
        self.assertEqual(classify(profile()), ["general"])


class ReportTests(unittest.TestCase):
    def setUp(self):
        _user, self.profiles = load_fixture(
            __import__("pathlib").Path(ROOT) / "examples" / "sample_profiles.json"
        )

    def test_summarize_counts(self):
        summary = summarize(self.profiles)
        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["organizations"], 1)
        self.assertEqual(summary["tag_counts"].get("developer"), 2)

    def test_render_report_contents(self):
        report = render_report("sample-owner", self.profiles)
        self.assertIn("# GitHub followers report: @sample-owner", report)
        self.assertIn("dev-taro", report)
        self.assertIn("Software engineer in Tokyo", report)
        self.assertIn("heuristics", report)

    def test_main_with_fixture_writes_report(self):
        import tempfile
        from pathlib import Path

        from bot.followers import main

        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "report.md")
            code = main([
                "--fixture",
                os.path.join(ROOT, "examples", "sample_profiles.json"),
                "--out", out,
            ])
            self.assertEqual(code, 0)
            text = Path(out).read_text(encoding="utf-8")
            self.assertIn("Followers checked: 4", text)


if __name__ == "__main__":
    unittest.main()
