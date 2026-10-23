import concurrent.futures
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from q4r.cli import main, verify
from q4r.core import Case, Target
from q4r.http import Client, Response
from q4r.providers import github_search
from q4r.social import (HTML_SITES, SOCIAL_PROVIDERS, bluesky, classify_html, html_profile,
                        mastodon, request_json, simple_api)


class ScriptedClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def fetch(self, source, url, headers=None):
        self.requests.append((source, url))
        response = next(self.responses)
        response.url = url
        return response


def response(value, status=200):
    body = value.encode() if isinstance(value, str) else json.dumps(value).encode()
    return Response(status, body, "E00001", "https://example.org/")


class SocialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.case = Case(Target("Example Researcher", "example"), self.root / "case")

    def check_html(self, source, body, handle="example", status=200):
        r = response(body, status)
        r.url = HTML_SITES[source][0].format(handle=handle)
        return classify_html(source, handle, r)

    def test_http_200_alone_never_means_profile_found(self):
        for source in HTML_SITES:
            with self.subTest(source=source):
                self.assertEqual(self.check_html(source, '<html><title>Welcome</title></html>')[0], "unconfirmed")

    def test_login_captcha_and_http_blocks(self):
        for body in ('<title>Log in to Instagram</title>', '<title>Just a moment...</title>', '<title>Verify you are human</title>'):
            self.assertEqual(self.check_html("instagram", body)[0], "blocked")
        for status in (401, 403, 429, 302):
            self.assertEqual(self.check_html("instagram", "", status=status)[0], "blocked")
        self.assertEqual(self.check_html("instagram", "", status=404)[0], "not_found")
        self.assertEqual(self.check_html("instagram", "", status=503)[0], "error")

    def test_explicit_public_profile_metadata(self):
        for source in ("instagram", "x", "pinterest"):
            endpoint = HTML_SITES[source][0].format(handle="example")
            body = f'<meta property="og:type" content="profile"><meta property="og:url" content="{endpoint}">' \
                   '<meta property="og:title" content="Example (@example)"><meta property="og:description" content="Public bio">'
            self.assertEqual(self.check_html(source, body)[0], "found")

    def test_metadata_for_another_handle_is_not_accepted(self):
        body = '<meta property="og:type" content="profile"><meta property="og:url" content="https://www.instagram.com/other/">' \
               '<meta property="og:title" content="Example (@example)">'
        self.assertEqual(self.check_html("instagram", body)[0], "unconfirmed")
        body = '<meta property="og:type" content="profile"><meta property="og:url" content="https://www.instagram.com/example/">' \
               '<meta property="og:title" content="Someone (@other)">'
        self.assertEqual(self.check_html("instagram", body)[0], "unconfirmed")

    def test_telegram_generic_contact_page_is_inconclusive(self):
        body = '<title>Telegram: Contact @example</title><meta property="og:title" content="Telegram: Contact @example">'
        self.assertEqual(self.check_html("telegram", body)[0], "unconfirmed")

    def test_telegram_public_channel(self):
        body = '<meta property="og:title" content="Example channel"><div class="tgme_page_title"></div><div class="tgme_page_extra">123 subscribers</div>'
        self.assertEqual(self.check_html("telegram", body)[0], "found")

    def test_youtube_requires_channel_id_and_matching_handle(self):
        channel = {"metadata":{"channelMetadataRenderer":{"externalId":"UCexample", "title":"Example", "ownerUrls":["https://www.youtube.com/@example"]}}}
        body = '<script>var ytInitialData = ' + json.dumps(channel) + ';</script>'
        self.assertEqual(self.check_html("youtube", body)[0], "found")
        channel["metadata"]["channelMetadataRenderer"]["ownerUrls"] = ["https://www.youtube.com/@other"]
        self.assertEqual(self.check_html("youtube", '<script>var ytInitialData = ' + json.dumps(channel) + ';</script>')[0], "unconfirmed")

    def test_facebook_requires_matching_profile_id_and_vanity(self):
        meta = '<meta property="og:url" content="https://www.facebook.com/example">'
        body = meta + '<script type="application/json">' + json.dumps({"data":{"userID":"1234", "userVanity":"example"}}) + '</script>'
        self.assertEqual(self.check_html("facebook", body)[0], "found")
        self.assertEqual(self.check_html("facebook", body.replace('"1234"', '"0"'))[0], "unconfirmed")

    def test_tiktok_structured_identity(self):
        body = '<link rel="canonical" href="https://www.tiktok.com/@example">' \
               '<script type="application/json">{"userInfo":{"user":{"uniqueId":"example","id":"123"}}}</script>'
        self.assertEqual(self.check_html("tiktok", body)[0], "found")

    def test_unconfirmed_pages_do_not_create_findings(self):
        client = ScriptedClient([response('<title>Welcome</title>')])
        self.assertEqual(html_profile(self.case, client, 5, "instagram")[0], "unconfirmed")
        self.assertEqual(self.case.findings, [])
        self.assertEqual(self.case.checks[0]["status"], "unconfirmed")

    def test_social_html_positive_retains_provenance(self):
        client = ScriptedClient([response('<meta property="og:type" content="profile"><meta property="og:url" content="https://x.com/example">' \
                                          '<meta property="profile:username" content="example"><meta property="og:title" content="Example">')])
        self.assertEqual(html_profile(self.case, client, 5, "x")[0], "ok")
        f = self.case.findings[0]
        self.assertEqual((f.source, f.kind, f.evidence_id, f.score), ("x", "public_profile", "E00001", 25))

    def test_bluesky_exact_and_name_search_without_api_key(self):
        actor = {"did":"did:plc:123", "handle":"example.bsky.social", "displayName":"Example Researcher", "description":"Public"}
        client = ScriptedClient([response(actor), response({"actors":[]}), response({"actors":[actor]})])
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(bluesky(self.case, client, 3)[0], "ok")
        self.assertEqual(len(client.requests), 3)
        self.assertTrue(all(f.attributes["did"] == "did:plc:123" for f in self.case.findings))

    def test_bluesky_preserves_underscores_for_search(self):
        self.case.target = Target(username="ss_demo")
        client = ScriptedClient([response({"actors":[]})])
        self.assertEqual(bluesky(self.case, client, 3)[0], "not_found")
        self.assertEqual(len(client.requests), 1)
        self.assertIn("q=ss_demo", client.requests[0][1])

    def test_mastodon_exact_lookup_without_token(self):
        client = ScriptedClient([response({"id":"123", "acct":"example", "url":"https://mastodon.social/@example", "display_name":"Example", "note":"<p>Public bio</p>"})])
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(mastodon(self.case, client, 5)[0], "ok")
        self.assertEqual(len(client.requests), 1)
        self.assertNotIn("<p>", self.case.findings[0].summary)

    def test_reddit_devto_and_hackernews_profiles(self):
        for source, payload in (("reddit", {"kind":"t2","data":{"id":"123","name":"example"}}),
                                ("devto", {"id":123,"username":"example","name":"Example"}),
                                ("hackernews", {"id":"example","about":"Public bio"})):
            with self.subTest(source=source):
                self.assertEqual(simple_api(self.case, ScriptedClient([response(payload)]), 5, source)[0], "ok")
        self.assertEqual(len(self.case.findings), 3)

    def test_hackernews_null_is_not_found(self):
        self.assertEqual(simple_api(self.case, ScriptedClient([response(None)]), 5, "hackernews")[0], "not_found")
        self.assertEqual(self.case.findings, [])

    def test_reddit_mismatched_username_rejected(self):
        with self.assertRaises(ValueError):
            simple_api(self.case, ScriptedClient([response({"kind":"t2","data":{"id":"123","name":"other"}})]), 5, "reddit")

    def test_json_challenge_is_not_reported_as_missing_account(self):
        for value, code, expected in (("<html>blocked</html>",403,"blocked"), ("<html>login</html>",200,"unconfirmed"), ({"error":"missing"},404,"not_found")):
            with self.subTest(expected=expected):
                d, eid, state = request_json(self.case, ScriptedClient([response(value,code)]), "reddit", "https://www.reddit.com/user/example/about.json", "example")
                self.assertEqual(state, expected)

    def test_parallel_case_writes_have_unique_ids_and_valid_hashes(self):
        def collect(i):
            eid = self.case.record("fixture", "https://example.org/" + str(i), 200, str(i).encode(), 0)
            self.case.add("fixture", "Profile " + str(i), "https://example.org/" + str(i), "", eid, "example")
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(collect, range(100)))
        self.assertEqual(len({x["id"] for x in self.case.requests}), 100)
        self.assertEqual(len({x.id for x in self.case.findings}), 100)
        for item in self.case.requests:
            self.assertEqual(hashlib.sha256((self.case.directory/item["file"]).read_bytes()).hexdigest(), item["sha256"])

    def test_parallel_cli_preserves_per_source_counts_and_manifest(self):
        barrier = threading.Barrier(2)
        def provider(source):
            def run(case, client, limit):
                barrier.wait(timeout=5)
                for n in range(10):
                    eid = case.record(source, "https://example.org", 200, b'{}', 0)
                    case.add(source, "Public profile", f"https://example.org/{source}/{n}", "", eid, "example")
                return "ok", "fixture complete"
            return run
        out = self.root / "parallel"
        with patch.dict("q4r.cli.PROVIDERS", {"first":provider("first"), "second":provider("second")}, clear=True), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["scan","--username","example","--sources","first,second","--workers","2","--out",str(out)]), 0)
            self.assertEqual(verify(out), 0)
        report = json.loads((out/"report.json").read_text())
        self.assertEqual({c["source"]:c["new_leads"] for c in report["coverage"]}, {"first":10,"second":10})

    def test_social_alias_and_offline_mode(self):
        out = self.root / "social-offline"
        with patch.object(Client,"fetch", side_effect=AssertionError("network")), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["scan","--username","example","--sources","social","--offline","--out",str(out)]),0)
        d = json.loads((out/"report.json").read_text())
        self.assertEqual({c["source"] for c in d["coverage"]}, set(SOCIAL_PROVIDERS))
        self.assertEqual(d["requests"], [])

    def test_untrusted_control_characters_do_not_break_graph_export(self):
        from q4r.report import write_report
        import xml.etree.ElementTree as ET
        self.case.add("fixture", "Bad\x00name", "https://example.org", "Bio\ud800", "E1", "example")
        write_report(self.case)
        ET.parse(self.case.directory/"graph.graphml")

    def test_public_link_graph_shares_url_node_and_preserves_evidence(self):
        from q4r.report import write_report
        import xml.etree.ElementTree as ET
        for source in ("github", "devto"):
            self.case.add(source, "Profile", "https://example.org/" + source, "", "E_" + source, "example",
                          {"links":[{"url":"https://example.org/shared", "relation":"explicitly published website; unverified ownership"},
                                    {"url":"javascript:alert(1)"}]})
        write_report(self.case)
        graph = ET.parse(self.case.directory/"graph.graphml")
        ns = {"g":"http://graphml.graphdrawing.org/xmlns"}
        nodes = [n for n in graph.findall(".//g:node",ns) if n.get("id").startswith("url_")]
        self.assertEqual(len(nodes),1)
        edges = [e for e in graph.findall(".//g:edge",ns) if e.get("target") == nodes[0].get("id")]
        self.assertEqual(len(edges),2)
        self.assertEqual({e.find("g:data[@key='evidence']",ns).text for e in edges}, {"E_github","E_devto"})

    def test_display_name_search_is_independent_of_unsupported_github_handle(self):
        client = unittest.mock.Mock()
        client.get.return_value = ({"items":[{"login":"public-example","html_url":"https://github.com/public-example"}]}, "E1")
        self.case.target = Target(name="Example Researcher",username="ss_demo")
        self.assertEqual(github_search(self.case,client,5)[0],"ok")
        self.assertIn("in%3Afullname",client.get.call_args.args[1])
        self.assertEqual(self.case.findings[0].score,0)


if __name__ == "__main__":
    unittest.main()
