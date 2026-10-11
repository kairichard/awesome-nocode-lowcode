import unittest
from unittest.mock import patch

from auto_merge import NeedsReview, eligible_entries, process, review_blocked


BASE = "# List\n\n## Apps\n\n- [Alpha](https://alpha.example) - First app.\n- [Zulu](https://zulu.example) - Last app.\n"
NEW = "- [Bravo](https://bravo.example) - Another app.\n"


class PolicyTests(unittest.TestCase):
    def check(self, after, before=BASE):
        return eligible_entries(before, after)

    def test_addition(self):
        self.assertEqual(self.check(BASE.replace("- [Zulu]", NEW + "- [Zulu]"))[0]["name"], "Bravo")

    def test_description_update(self):
        self.assertEqual(len(self.check(BASE.replace("First app.", "Updated description."))), 1)

    def test_rejects_non_entry_changes(self):
        for after in [BASE.replace("# List", "# Different"), BASE + "<script>bad</script>\n", BASE + "## New category\n" + NEW]:
            with self.subTest(after=after), self.assertRaises(NeedsReview):
                self.check(after)

    def test_rejects_removal_rename_and_url_replacement(self):
        for after in [BASE.replace(BASE.splitlines(True)[4], ""), BASE.replace("Alpha]", "Aardvark]"), BASE.replace("alpha.example", "evil.example")]:
            with self.subTest(after=after), self.assertRaises(NeedsReview):
                self.check(after)

    def test_rejects_bad_placement_and_duplicate(self):
        for after in [BASE + NEW, BASE.replace("- [Zulu]", NEW.replace("Bravo", "Alpha") + "- [Zulu]"), BASE.replace("- [Zulu]", NEW.replace("bravo.example", "www.alpha.example/") + "- [Zulu]")]:
            with self.subTest(after=after), self.assertRaises(NeedsReview):
                self.check(after)

    def test_rejects_extra_links_markup_and_private_urls(self):
        for entry in [NEW.replace("Another app.", "See [other](https://evil.example)."), NEW.replace("Another app.", "<img src=x>"), NEW.replace("bravo.example", "127.0.0.1"), NEW.replace("bravo.example", "localhost"), NEW.replace("bravo.example", "user:pass@bravo.example"), NEW.replace("bravo.example", "bravo.example?ref=affiliate")]:
            with self.subTest(entry=entry), self.assertRaises(NeedsReview):
                self.check(BASE.replace("- [Zulu]", entry + "- [Zulu]"))

    def test_does_not_require_reordering_untouched_entries(self):
        before = BASE.replace("- [Alpha]", "- [Zzz](https://zzz.example) - Old misplaced entry.\n- [Alpha]")
        self.assertEqual(len(self.check(before.replace("- [Zulu]", NEW + "- [Zulu]"), before)), 1)

    def test_rejects_bulk_changes(self):
        entries = "".join(NEW.replace("Bravo", name).replace("bravo.example", name.lower() + ".example") for name in ["Bravo", "Charlie", "Delta", "Echo"])
        with self.assertRaises(NeedsReview):
            self.check(BASE.replace("- [Zulu]", entries + "- [Zulu]"))

    def test_latest_substantive_review_controls(self):
        def review(state):
            return {"user": {"login": "maintainer"}, "state": state}
        self.assertTrue(review_blocked([review("CHANGES_REQUESTED"), review("COMMENTED")]))
        self.assertFalse(review_blocked([review("CHANGES_REQUESTED"), review("APPROVED")]))


class MergeSafetyTests(unittest.TestCase):
    def execute(self, changed=False, blocked=False):
        pr = {"number": 7, "head": {"sha": "reviewed-head"}, "base": {"ref": "master"}, "draft": False, "state": "open", "labels": []}
        reads = 0
        writes = []

        def fake_api(path, method="GET", data=None, **kwargs):
            nonlocal reads
            if method != "GET":
                writes.append((path, data))
                return {"merged": True, "sha": "merged-commit"}
            if path == "pulls/7":
                reads += 1
                return dict(pr, head={"sha": "new-head"}) if changed and reads > 1 else pr
            if path == "pulls/7/files":
                return [{"filename": "README.md", "status": "modified"}]
            if path == "pulls/7/reviews":
                return []
            if path == "git/ref/heads/master":
                return {"object": {"sha": "reviewed-base"}}
            self.fail("Unexpected API request: " + path)

        with patch("auto_merge.api", side_effect=fake_api), patch("auto_merge.snapshot", return_value=("reviewed-base", [{"name": "Bravo", "url": "https://bravo.example"}])), patch("auto_merge.check_link"), patch("auto_merge.report"), patch("auto_merge.other_checks", side_effect=NeedsReview("A check failed") if blocked else None):
            result = process(7, True)
        return result, writes

    def test_head_change_prevents_merge(self):
        result, writes = self.execute(changed=True)
        self.assertIn("NEEDS REVIEW", result)
        self.assertEqual(writes, [])

    def test_failing_checks_prevent_merge(self):
        result, writes = self.execute(blocked=True)
        self.assertIn("NEEDS REVIEW", result)
        self.assertEqual(writes, [])

    def test_merge_is_bound_to_reviewed_head(self):
        result, writes = self.execute()
        self.assertIn("MERGED", result)
        self.assertEqual(writes, [("pulls/7/merge", {"sha": "reviewed-head", "merge_method": "squash"})])


if __name__ == "__main__":
    unittest.main()
