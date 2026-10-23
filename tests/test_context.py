from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from q4r.cli import main, verify
from q4r.core import Case, Target, build_plan, score_match
from q4r.results import account_lines, email_summary_line


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_country_is_not_an_identity_and_aliases_are_bounded_and_validated(self):
        for values in (dict(country="Jordan"), dict(username="demo", country="x"*81),
                       dict(aliases="demo"), dict(aliases=["bad/handle"]),
                       dict(aliases=["x"]*6), dict(aliases=[1])):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Target(**values)
        target = Target(username="@demo", country="  المملكة   الأردنية  ", aliases=["@demo", "@other", "other"])
        self.assertEqual(target.usernames(), ["demo", "other"])
        self.assertEqual(target.country, "المملكة الأردنية")
        self.assertEqual(Target(aliases=["only_alias"]).label(), "only_alias")

    def test_full_name_is_never_split_and_country_is_never_searched_alone(self):
        name = "أحمد محمود حسن العلي"
        target = Target(name=name, username="demo", country="الأردن", aliases=["other"])
        plan = list(build_plan(target, [{"domain":"instagram.com"}]))
        queries = [p["query"] for p in plan]
        self.assertEqual(queries[0], '"' + name + '" "الأردن"')
        self.assertIn('"demo" "الأردن"', queries)
        self.assertIn('"other" "الأردن"', queries)
        self.assertIn('"' + name + '"', queries)
        self.assertFalse(any(q == '"الأردن"' for q in queries))
        self.assertFalse(any('"محمود"' in q or '"حسن"' in q for q in queries))
        self.assertTrue(all('"' + name + '"' in q for q in queries if "أحمد" in q))
        self.assertIn('site:instagram.com "other" "الأردن"', queries)

    def test_email_queries_preserve_full_address_and_country_fallback(self):
        plan = list(build_plan(Target(email="me@example.org", country="Jordan"), []))
        self.assertEqual([p["query"] for p in plan], ['"me@example.org" "Jordan"', '"me@example.org" "الأردن"', '"me@example.org"'])
        self.assertEqual([p["input"] for p in plan], ["email+country", "email+country_variant", "email"])

    def test_country_input_cannot_inject_search_operators_outside_quotes(self):
        plan = list(build_plan(Target(username="demo", country='Jordan" OR "elsewhere'), []))
        self.assertEqual(plan[0]["query"].count('"'), 4)
        self.assertEqual(plan[-1]["query"], '"demo"')

    def test_alias_matches_score_once_and_country_is_not_identity_evidence(self):
        target = Target(username="demo", country="Jordan", aliases=["other"])
        self.assertEqual(score_match(target, "Jordan", "Jordan", {"username":"other"})[0], 25)
        self.assertEqual(score_match(target, "Jordan", "Jordan", {"country":"Jordan"})[0], 0)

    def test_views_keep_per_lookup_inputs_and_parent_evidence_scoring(self):
        case = Case(Target(name="Example Researcher", username="demo", country="Jordan", aliases=["other"]), self.root/"case")
        view = case.for_username("other", include_name=False)
        self.assertEqual(view.target.name, "")
        self.assertEqual(view.target.country, "Jordan")
        view.add("github", "Other", "https://github.com/other", "", "E1", "other", {"username":"other"})
        self.assertEqual(case.findings[0].score, 25)
        self.assertEqual(case.target.username, "demo")
        self.assertEqual(case.target.name, "Example Researcher")

    def test_parallel_sources_query_each_supplied_handle_without_cross_talk(self):
        barrier = threading.Barrier(2)
        calls = {"github":[], "gitlab":[]}
        def provider(source):
            def collect(case, client, limit):
                handle = case.target.username
                calls[source].append((handle, case.target.name))
                barrier.wait(timeout=5)
                eid = case.record(source, "https://example.org/"+handle, 200, b"{}", 0)
                case.add(source, handle, f"https://example.org/{source}/{handle}", "", eid, handle, {"username":handle})
                return "ok", "public fixture"
            return collect
        directory = self.root/"parallel"
        with patch.dict("q4r.cli.PROVIDERS", {s:provider(s) for s in calls}, clear=True), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["scan", "--name", "Example Researcher", "--username", "demo", "--alias", "other",
                                   "--country", "Jordan", "--sources", "github,gitlab", "--out", str(directory)]), 0)
            self.assertEqual(verify(directory), 0)
        self.assertEqual(calls["github"], [("demo", "Example Researcher"), ("other", "")])
        self.assertEqual(calls["github"], calls["gitlab"])
        data = json.loads((directory/"report.json").read_text())
        self.assertEqual(data["target"]["username"], "demo")
        self.assertEqual(data["target"]["aliases"], ["other"])
        self.assertEqual({x["new_leads"] for x in data["coverage"]}, {2})
        self.assertEqual({len(x["lookups"]) for x in data["coverage"]}, {2})

    def test_failure_of_one_handle_does_not_prevent_another_lookup(self):
        def provider(case, client, limit):
            if case.target.username == "demo":
                raise ValueError("fixture failure")
            case.add("github", "Other", "https://github.com/other", "", "E1", "other", {"username":"other"})
            return "ok", "fixture"
        directory = self.root/"partial"
        with patch.dict("q4r.cli.PROVIDERS", {"github":provider}, clear=True), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["scan", "--username", "demo", "--alias", "other", "--sources", "github", "--out", str(directory)]), 3)
        data = json.loads((directory/"report.json").read_text())
        self.assertEqual(len(data["findings"]), 1)
        self.assertEqual(data["coverage"][0]["status"], "partial")

    def test_email_row_is_visible_in_terminal_text_and_html_even_with_zero_results(self):
        directory = self.root/"offline"
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["scan", "--email", "me@example.org", "--country", "Jordan", "--offline", "--out", str(directory)]), 0)
            self.assertEqual(verify(directory), 0)
        data = json.loads((directory/"report.json").read_text())
        row = email_summary_line(data)
        self.assertTrue(row.startswith("Email: me@example.org | Instagram:"))
        self.assertIn(row, output.getvalue())
        self.assertEqual((directory/"accounts.txt").read_text(encoding="utf-8").splitlines()[0], row)
        self.assertIn(row, (directory/"report.html").read_text(encoding="utf-8"))
        self.assertIn("Country keyword (supplied, unverified): Jordan", output.getvalue())
        self.assertTrue(output.getvalue().splitlines()[-2].startswith("Accounts text file:"))
        self.assertEqual(data["requests"], [])

    def test_unchecked_platforms_are_explicit_and_do_not_claim_no_accounts(self):
        text = account_lines({"target":{}, "findings":[], "coverage":[]})
        self.assertIn("Facebook: ? [not checked", text)
        self.assertNotIn("Facebook: X", text)

    def test_optional_prompt_inputs_accept_arabic_commas_and_alias_only_search(self):
        directory = self.root/"interactive"
        factory = lambda target, ignored: Case(target, directory)
        def provider(case, client, limit):
            self.assertIn(case.target.username, ("first", "second"))
            return "ok", "fixture"
        with patch("q4r.cli.automatic_update", return_value=None), patch("q4r.cli.Case", side_effect=factory), \
             patch.dict("q4r.cli.PROVIDERS", {"github":provider}, clear=True), \
             patch.dict("os.environ", {"BRAVE_SEARCH_API_KEY":""}), \
             patch("builtins.input", side_effect=["", "", "", "Jordan", "@first،second"]), redirect_stdout(io.StringIO()):
            self.assertEqual(main([]), 0)
        data = json.loads((directory/"report.json").read_text())
        self.assertEqual(data["target"]["aliases"], ["first", "second"])
        self.assertEqual(len(data["coverage"][0]["lookups"]), 2)


if __name__ == "__main__":
    unittest.main()
