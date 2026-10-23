from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from q4r.browser import browser_tasks
from q4r.cli import main
from q4r.core import Case, Target, build_plan, score_match
from q4r.http import Response
from q4r.names import country_context, name_forms, name_match
from q4r.providers import github_search
from q4r.results import email_summary_line
from q4r.social import bluesky, mastodon


class ScriptedClient:
    def __init__(self, values):
        self.values = iter(values)
        self.urls = []

    def fetch(self, source, url, headers=None):
        self.urls.append(url)
        return Response(200, json.dumps(next(self.values), ensure_ascii=False).encode(), "E1", url)


class NameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_known_latin_name_keeps_original_and_adds_labeled_arabic_guess(self):
        forms = name_forms("Qusai Mohammad")
        self.assertEqual(forms[0], dict(value="Qusai Mohammad", origin="supplied"))
        self.assertIn("قصي محمد", [x["value"] for x in forms])
        self.assertIn("guess", forms[1]["origin"])

    def test_arabic_to_latin_suggestions_preserve_all_name_parts(self):
        forms = name_forms("قصي محمد العلي")
        self.assertIn("Qusai Mohammad Alali", [x["value"] for x in forms])
        self.assertIn("Qusay Muhammad Al-Ali", [x["value"] for x in forms])
        self.assertLessEqual(len(forms), 3)
        self.assertTrue(all(len(x["value"].split()) >= 3 for x in forms))

    def test_portuguese_context_preserves_names_and_adds_country_and_accent_forms(self):
        target = Target(name="José Silva", country="البرازيل")
        self.assertEqual(country_context(target.country)["language"], "Portuguese")
        queries = [p["query"] for p in build_plan(target, [])]
        self.assertIn('"José Silva" "Brasil"', queries)
        self.assertIn('"jose silva"', queries)
        self.assertEqual(target.name, "José Silva")
        self.assertEqual(country_context("السعودية")["language"], "Arabic")
        self.assertIn("السعودية", country_context("Saudi Arabia")["terms"])

    def test_unknown_and_very_long_names_do_not_create_unbounded_guesses(self):
        self.assertEqual(len(name_forms("Zzz Qqq")), 1)
        self.assertEqual(len(name_forms("محمد "*100 + "العلي")), 1)
        self.assertEqual(len(name_forms("قصي محمد", expand=False)), 1)

    def test_manual_spellings_are_validated_and_work_without_automatic_expansion(self):
        target = Target(name="Qusai Mohammad", name_variants=["قصي محمد"], expand_names=False)
        self.assertEqual(len(target.name_forms()), 2)
        self.assertEqual(target.name_forms()[1]["origin"], "supplied spelling; ownership unverified")
        for values in (dict(name_variants=["قصي محمد"]), dict(name="Qusai Mohammad", name_variants=["قصي"]),
                       dict(name="Qusai Mohammad", name_variants=["قصي محمد"]*4)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Target(**values)
        case = Case(target, self.root/"case")
        self.assertEqual(case.for_username("other", include_name=False).target.name_forms(), [])

    def test_longer_names_are_candidates_and_single_or_partial_token_matches_are_rejected(self):
        target = Target(name="Qusai Mohammad")
        self.assertEqual(name_match(target, "Qusai Mohammad")[0], "exact")
        self.assertEqual(name_match(target, "Dr Qusai Mohammad Else")[0], "extended")
        self.assertEqual(name_match(target, "قصي محمد اليسير")[0], "spelling_candidate")
        for value in ("Qusai", "Mohammad Other", "Qusai Mohammadi", "Qusai Unrelated Mohammad"):
            self.assertIsNone(name_match(target, value))

    def test_exact_extended_and_guessed_names_have_distinct_evidence_scores(self):
        target = Target(name="Qusai Mohammad", email="me@example.org")
        scores = [score_match(target, "", "", {"name":name})[0] for name in ("Qusai Mohammad", "Qusai Mohammad Else", "قصي محمد")]
        self.assertEqual(scores, [25, 20, 10])
        case = Case(target, self.root/"case")
        eid = case.record("instagram", "https://www.instagram.com/example/", 200, b"public profile fixture", 0)
        case.add("instagram", "قصي محمد", "https://www.instagram.com/example/", "", eid, "قصي محمد",
                 {"username":"example", "name":"قصي محمد"}, kind="public_profile")
        self.assertIn("Instagram: غير مثبت", email_summary_line(case.data()))

    def test_bluesky_filters_single_name_rows_retains_longer_names_and_deduplicates(self):
        case = Case(Target(name="Qusai Mohammad"), self.root/"case")
        actors = [dict(did="did:1", handle="one.bsky.social", displayName="Qusai Mohammad Else"),
                  dict(did="did:2", handle="two.bsky.social", displayName="Qusai"),
                  dict(did="did:3", handle="three.bsky.social", displayName="قصي محمد اليسير"),
                  dict(did="did:4", handle="handle.invalid", displayName="Qusai Mohammad")]
        client = ScriptedClient([{"actors":actors}, {"actors":actors}])
        self.assertEqual(bluesky(case, client, 5)[0], "ok")
        self.assertEqual({f.attributes["username"] for f in case.findings}, {"one.bsky.social", "three.bsky.social"})
        self.assertEqual(len(case.findings), 2)
        self.assertEqual({parse_qs(urlsplit(url).query)["q"][0] for url in client.urls}, {"Qusai Mohammad", "قصي محمد"})
        self.assertTrue(any(c["status"] == "filtered" for c in case.checks))

    def test_exact_supplied_handle_is_not_rejected_for_a_different_display_name(self):
        case = Case(Target(name="Qusai Mohammad", username="known"), self.root/"case")
        actor = dict(did="did:1", handle="known.bsky.social", displayName="Other display name")
        client = ScriptedClient([actor, {"actors":[]}, {"actors":[]}, {"actors":[]}])
        self.assertEqual(bluesky(case, client, 5)[0], "ok")
        self.assertEqual(len(case.findings), 1)

    def test_only_filtered_results_are_inconclusive_not_absent(self):
        case = Case(Target(name="Example Researcher"), self.root/"case")
        client = ScriptedClient([{"actors":[dict(did="did:1", handle="other.bsky.social", displayName="Example")]}])
        self.assertEqual(bluesky(case, client, 5)[0], "unconfirmed")
        self.assertEqual(case.findings, [])

    def test_mastodon_never_truncates_a_full_name_to_fit_the_query_budget(self):
        case = Case(Target(name="A"*300 + " Last"), self.root/"case")
        client = ScriptedClient([])
        with patch.dict("os.environ", {"Q4R_MASTODON_TOKEN":"fixture"}):
            self.assertEqual(mastodon(case, client, 5)[0], "skipped")
        self.assertEqual(client.urls, [])

    def test_mastodon_name_search_uses_both_scripts_and_filters_single_name_hits(self):
        case = Case(Target(name="Qusai Mohammad"), self.root/"case")
        client = ScriptedClient([[dict(acct="first", url="https://mastodon.social/@first", display_name="Qusai Mohammad Else"),
                                   dict(acct="wrong", url="https://mastodon.social/@wrong", display_name="Qusai")],
                                  [dict(acct="second", url="https://mastodon.social/@second", display_name="قصي محمد اليسير")]])
        with patch.dict("os.environ", {"Q4R_MASTODON_TOKEN":"fixture"}):
            self.assertEqual(mastodon(case, client, 5)[0], "ok")
        self.assertEqual({f.attributes["username"] for f in case.findings}, {"first", "second"})
        self.assertEqual({parse_qs(urlsplit(url).query)["q"][0] for url in client.urls}, {"Qusai Mohammad", "قصي محمد"})

    def test_browser_budget_rotates_distinct_queries_then_revisits_other_engines(self):
        plan = [{"query":q} for q in ('"Qusai Mohammad"', '"قصي محمد"', '"Qusai Mohammad"')]
        tasks = list(browser_tasks(plan, ["google", "bing", "duckduckgo"]))
        self.assertEqual([x[0]["query"] for x in tasks[:2]], ['"Qusai Mohammad"', '"قصي محمد"'])
        self.assertEqual(len(tasks), 6)
        self.assertEqual({(x[0]["query"], x[1]) for x in tasks}, {(q, e) for q in ('"Qusai Mohammad"', '"قصي محمد"') for e in ("google", "bing", "duckduckgo")})

    def test_github_queries_each_spelling_without_claiming_a_verified_display_name(self):
        case = Case(Target(name="Qusai Mohammad"), self.root/"case")
        client = Mock()
        client.get.return_value = ({"items":[{"login":"example", "html_url":"https://github.com/example"}]}, "E1")
        self.assertEqual(github_search(case, client, 5)[0], "ok")
        self.assertEqual(client.get.call_count, 2)
        self.assertEqual(len(case.findings), 1)
        self.assertNotIn("name", case.findings[0].attributes)
        self.assertEqual(case.findings[0].score, 0)

    def test_cli_saves_and_displays_spelling_provenance_with_no_network_in_offline_mode(self):
        directory = self.root/"offline"
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["scan", "--name", "Qusai Mohammad", "--country", "Saudi Arabia", "--email", "me@example.org",
                                   "--offline", "--out", str(directory)]), 0)
        data = json.loads((directory/"report.json").read_text(encoding="utf-8"))
        self.assertEqual(data["name_search"]["country_context"]["language"], "Arabic")
        self.assertIn("قصي محمد", [x["value"] for x in data["name_search"]["forms"]])
        self.assertIn("guess", (directory/"report.html").read_text(encoding="utf-8"))
        self.assertIn("Public email association only", output.getvalue())
        self.assertIn("Public profile URLs retrieved: 0", output.getvalue())
        self.assertEqual(data["requests"], [])


if __name__ == "__main__":
    unittest.main()
