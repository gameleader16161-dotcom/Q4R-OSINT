import hashlib
import io
import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from q4r.cli import main, verify
from q4r.core import Case, Target, build_plan, load_sites, safe_url, score_match
from q4r.http import Client, MAX_BYTES, SourceError
from q4r.providers import archive, brave, github, gitlab, keybase, openlibrary, wayback
from q4r.report import write_report


class Response:
    status = 200

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, n):
        return self.body[:n]


class FakeClient:
    def __init__(self, data):
        self.data, self.urls = data, []

    def get(self, source, url, headers=None):
        self.urls.append(url)
        return self.data, "E00001"


class Q4RTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.case = Case(Target("Example Researcher", "example", "me@example.org"), self.root / "case")

    def test_unicode_long_names(self):
        name = "محمد " * 300 + "العلي"
        self.assertEqual(Target(name=name).name, name)
        self.assertEqual(Target(name="محمد أحمد العلي").name, "محمد أحمد العلي")

    def test_invalid_inputs(self):
        for values in ({}, {"name": "Single"}, {"email": "bad"}, {"username": "a/b"}, {"name": "a\x00 b"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Target(**values)

    def test_username_only_and_email_only(self):
        self.assertEqual(Target(username="@example").username, "example")
        self.assertEqual(Target(email="a@example.org").email, "a@example.org")

    def test_scores_are_not_query_echoes(self):
        self.assertEqual(score_match(self.case.target, "", "", {})[0], 0)
        self.assertEqual(score_match(self.case.target, "", "", {"username": "example"})[0], 25)
        self.assertEqual(score_match(self.case.target, "", "", {"username": "example", "name": "Example Researcher", "email": "me@example.org"})[0], 90)

    def test_unsafe_urls(self):
        for url in ("javascript:alert(1)", "data:text/html,evil", "https://user:pass@example.org", "file:///tmp/a"):
            self.assertEqual(safe_url(url), "")
        self.assertEqual(safe_url("https://example.org/a#frag"), "https://example.org/a")

    def test_plan_not_evidence(self):
        plan = list(build_plan(self.case.target, load_sites()))
        self.assertEqual(len(load_sites()), 48)
        self.assertTrue(all(p["status"] == "manual_search_link" for p in plan))
        self.assertEqual(len(self.case.findings), 0)
        self.assertIn('"Example Researcher"', plan[0]["query"])

    def test_registry_validation(self):
        registry = self.root / "sites.json"
        registry.write_text('[{"domain":"127.0.0.1/x"}]')
        with self.assertRaises(ValueError):
            load_sites(registry)

    def test_github_parses_public_profile(self):
        client = FakeClient({"login": "example", "name": "Example Researcher", "html_url": "https://github.com/example", "bio": "Hello"})
        self.assertEqual(github(self.case, client, 5)[0], "ok")
        self.assertEqual(self.case.findings[0].score, 50)
        self.assertEqual(self.case.findings[0].evidence_id, "E00001")

    def test_provider_schema_fixtures(self):
        fixtures = [
            (gitlab, [{"username":"example", "name":"Example", "web_url":"https://gitlab.com/example"}]),
            (keybase, {"status":{"code":0}, "them":[None, {"basics":{"username":"example"}}]}),
            (archive, {"response":{"docs":[{"identifier":"demo", "title":"Example Researcher"}]}}),
            (openlibrary, {"docs":[{"key":"/works/OL1W", "title":"Book", "author_name":["Example Researcher"]}]})]
        for fn, payload in fixtures:
            with self.subTest(source=fn.__name__):
                before = len(self.case.findings)
                client = FakeClient(payload)
                self.assertEqual(fn(self.case, client, 5)[0], "ok")
                self.assertEqual(len(self.case.findings), before + 1)
                if fn == archive:
                    query = parse_qs(urlsplit(client.urls[0]).query)
                    self.assertEqual(query["fl[]"], ["identifier", "title", "description", "creator"])

    def test_no_key_is_explicit_skip(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(brave(self.case, FakeClient({}), 5, 10)[0], "skipped")

    def test_brave_budget_and_unsafe_url(self):
        self.case.plan = list(build_plan(self.case.target, load_sites()))
        client = FakeClient({"web":{"results":[{"title":"Lead", "url":"https://example.org/u"}, {"url":"javascript:alert(1)"}]}})
        with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY":"dummy"}):
            brave(self.case, client, 5, 2)
        self.assertEqual(len(client.urls), 2)
        self.assertEqual(len(self.case.findings), 2)  # Different query provenance retained.
        self.assertEqual(self.case.plan[2]["status"], "manual_search_link")

    def test_wayback_from_retrieved_urls(self):
        self.case.add("github", "Profile", "https://github.com/example", "", "E00001", "example")
        client = FakeClient({"archived_snapshots":{"closest":{"available":True, "url":"https://web.archive.org/web/20200101/https://github.com/example", "timestamp":"20200101"}}})
        wayback(self.case, client, 5)
        self.assertEqual(self.case.findings[-1].kind, "archive_snapshot")
        self.assertIn("url=https", client.urls[0])

    def test_evidence_and_manifest_tamper(self):
        eid = self.case.record("test", "https://example.org", 200, b'{"ok":true}', 0.1)
        self.assertEqual(self.case.requests[0]["sha256"], hashlib.sha256(b'{"ok":true}').hexdigest())
        write_report(self.case)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(verify(self.case.directory), 0)
            (self.case.evidence_dir / (eid + ".bin")).write_bytes(b"modified")
            self.assertEqual(verify(self.case.directory), 1)

    def test_reports_escape_untrusted_text(self):
        self.case.add("test", '<script>alert(1)</script>', "https://example.org", '<img src=x onerror="alert(1)">', "E1", '=HYPERLINK("evil")')
        write_report(self.case)
        document = (self.case.directory / "report.html").read_text(encoding="utf-8")
        self.assertNotIn('<script>alert(1)</script>', document)
        self.assertIn('&lt;script&gt;', document)
        self.assertIn("'=HYPERLINK", (self.case.directory / "findings.csv").read_text(encoding="utf-8-sig"))
        ET.parse(self.case.directory / "graph.graphml")

    def test_deduplicate_and_keep_provenance(self):
        for query in ("a", "a", "b"):
            self.case.add("x", "Title", "https://example.org", "", "E1", query)
        self.assertEqual(len(self.case.findings), 2)

    def test_transport_allowlist(self):
        client = Client(self.case, interval=0)
        for url in ("http://api.github.com/users/x", "https://127.0.0.1", "https://api.github.com:444/x", "https://api.github.com.evil.org"):
            with self.assertRaises(SourceError):
                client.get("test", url)

    def test_http_json_and_no_credentials_saved(self):
        client = Client(self.case, interval=0)
        with patch.object(client.opener, "open", return_value=Response(b'{"login":"example"}')):
            data, eid = client.get("github", "https://api.github.com/users/example", {"Authorization":"Bearer SECRET"})
        self.assertEqual(data["login"], "example")
        self.assertNotIn("SECRET", (self.case.directory / "requests.jsonl").read_text())

    def test_http_errors_and_non_json_are_evidence(self):
        client = Client(self.case, interval=0)
        error = HTTPError("https://api.github.com/users/x", 403, "Forbidden", {}, io.BytesIO(b'{"message":"blocked"}'))
        with patch.object(client.opener, "open", side_effect=error), self.assertRaises(SourceError):
            client.get("github", "https://api.github.com/users/x")
        self.assertEqual(self.case.requests[-1]["status"], 403)
        with patch.object(client.opener, "open", return_value=Response(b'<html>captcha</html>')), self.assertRaises(SourceError):
            client.get("github", "https://api.github.com/users/x")
        self.assertEqual(len(self.case.requests), 2)

    def test_retry_respects_long_retry_after(self):
        client = Client(self.case, interval=0)
        error = HTTPError("https://api.github.com/users/x", 429, "Rate limit", {"Retry-After":"120"}, io.BytesIO(b'{}'))
        with patch.object(client.opener, "open", side_effect=error) as request, self.assertRaises(SourceError):
            client.get("github", "https://api.github.com/users/x")
        self.assertEqual(request.call_count, 1)

    def test_bounded_retry_recovers(self):
        client = Client(self.case, interval=0)
        error = HTTPError("https://api.github.com/users/x", 503, "Unavailable", {}, io.BytesIO(b'{}'))
        with patch.object(client.opener, "open", side_effect=[error, Response(b'{}')]), patch("q4r.http.time.sleep"):
            client.get("github", "https://api.github.com/users/x")
        self.assertEqual([r["status"] for r in self.case.requests], [503, 200])

    def test_oversized_body_rejected(self):
        client = Client(self.case, interval=0)
        with patch.object(client.opener, "open", return_value=Response(b'x' * (MAX_BYTES + 1))), self.assertRaises(SourceError):
            client.get("github", "https://api.github.com/users/x")
        self.assertEqual(self.case.requests[-1]["bytes"], MAX_BYTES)

    def test_offline_cli_never_opens_network_or_ai(self):
        out = self.root / "offline"
        with patch("q4r.http.Client.get", side_effect=AssertionError("network")), patch("q4r.ai.summarize", side_effect=AssertionError("AI")), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["scan", "--name", "Example Researcher", "--offline", "--ai-model", "demo", "--out", str(out)]), 0)
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["requests"], [])
        self.assertEqual(report["findings"], [])
        self.assertTrue(report["plan"])

    def test_failure_does_not_drop_other_source(self):
        def transport(client, source, url, headers=None):
            if source == "github":
                raise SourceError("HTTP 403")
            return [{"username":"example", "web_url":"https://gitlab.com/example"}], "E1"
        out = self.root / "partial"
        with patch.object(Client, "get", transport), redirect_stdout(io.StringIO()):
            code = main(["scan", "--username", "example", "--sources", "github,gitlab", "--out", str(out)])
        self.assertEqual(code, 3)
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(len(report["findings"]), 1)
        self.assertEqual([x["status"] for x in report["coverage"]], ["error", "ok"])

    def test_existing_case_never_overwritten(self):
        with self.assertRaises(FileExistsError):
            Case(self.case.target, self.case.directory)

    def test_ai_rejects_invented_citations(self):
        from q4r.ai import summarize
        response = json.dumps({"response":json.dumps({"observations":[{"text":"claim", "evidence_ids":["F99999"]}]})}).encode()
        with patch("q4r.ai.build_opener") as opener:
            opener.return_value.open.return_value = Response(response)
            with self.assertRaises(ValueError):
                summarize(self.case, "local-model")
        self.assertFalse((self.case.directory / "ai-summary.json").exists())

    def test_ai_rejects_cloud_models(self):
        from q4r.ai import summarize
        with self.assertRaises(ValueError):
            summarize(self.case, "demo-cloud")


if __name__ == "__main__":
    unittest.main()
