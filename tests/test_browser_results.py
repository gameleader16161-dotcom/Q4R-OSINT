import base64
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from q4r.browser import engine_url, page_state, result_url, search_pages
from q4r.cli import main, verify
from q4r.core import Case, Target
from q4r.report import write_report
from q4r.results import account_from_url, account_lines, write_accounts


class FakePage:
    def __init__(self, body="Search results",status=200):
        self.body,self.status,self.url,self.visits = body,status,"about:blank",[]
        self.closed = False

    def goto(self,url,**kwargs):
        self.url=url
        self.visits.append(url)
        return Mock(status=self.status)

    def title(self):
        return "Search"

    def locator(self,selector):
        node = Mock()
        node.first.wait_for.return_value = None
        node.inner_text.return_value = self.body
        return node

    def close(self):
        self.closed=True

    def wait_for_timeout(self,delay):
        pass


class BrowserResultsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.case = Case(Target(name="Example Researcher",username="ss_demo"),self.root/"case")
        self.case.plan = [{"query":'"Example Researcher"',"url":engine_url("google",'"Example Researcher"'),"status":"manual_search_link"},
                          {"query":'"ss_demo"',"url":engine_url("google",'"ss_demo"'),"status":"manual_search_link"}]

    def test_known_search_redirects_are_decoded_without_requests(self):
        target="https://www.instagram.com/ss_demo/"
        b64=base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
        samples = [("google","/url?"+urlencode({"q":target})),("bing","/ck/a?"+urlencode({"u":"a1"+b64})),
                   ("duckduckgo","/l/?"+urlencode({"uddg":target}))]
        for engine,href in samples:
            with self.subTest(engine=engine):
                self.assertEqual(result_url(href,engine),target)

    def test_engine_internal_links_and_private_destinations_are_not_findings(self):
        for href in ("/search?q=x","javascript:alert(1)","https://127.0.0.1/x","https://server.local/x","https://user:pass@example.org/"):
            with self.subTest(href=href):
                self.assertEqual(result_url(href,"google"),"")
        self.assertEqual(result_url("/ck/a?u=invalid","bing"),"")

    def test_browser_gate_and_no_result_states(self):
        u=engine_url("google","example")
        self.assertEqual(page_state("google",u,"Search","unusual traffic",200,10)[0],"blocked")
        self.assertEqual(page_state("google",u,"Search","",403,10)[0],"blocked")
        self.assertEqual(page_state("google","https://consent.google.com/","Consent","",200,10)[0],"blocked")
        self.assertEqual(page_state("google",u,"Search","did not match any documents",200,0)[0],"not_found")
        self.assertEqual(page_state("google",u,"Search","",200,0)[0],"unconfirmed")

    def test_budget_visible_results_and_browser_evidence_type(self):
        page=FakePage(); context=Mock();context.new_page.return_value=page
        rows=[dict(title="Example profile",url="https://www.instagram.com/ss_demo/",snippet="Public search snippet")]
        with patch("q4r.browser.extract_visible",return_value=rows),redirect_stdout(io.StringIO()):
            self.assertEqual(search_pages(self.case,context,["google","bing","duckduckgo"],2,5,15,interval=0)[0],"ok")
        self.assertEqual(len(page.visits),2)
        self.assertTrue(page.closed)
        self.assertEqual(len(self.case.findings),2)
        self.assertTrue(all(r["evidence_type"]=="browser_rendered_observation" for r in self.case.requests))
        self.assertTrue(all(f.kind=="web_search_lead" for f in self.case.findings))

    def test_challenged_engine_is_not_retried_and_creates_no_fake_accounts(self):
        page=FakePage("unusual traffic");context=Mock();context.new_page.return_value=page
        with patch("q4r.browser.extract_visible",return_value=[dict(title="Untrusted",url="https://example.org/",snippet="")]),redirect_stdout(io.StringIO()):
            self.assertEqual(search_pages(self.case,context,["google"],5,5,15,interval=0)[0],"blocked")
        self.assertEqual(len(page.visits),1)
        self.assertEqual(self.case.findings,[])

    def test_account_text_distinguishes_retrieved_absent_and_blocked(self):
        self.case.add("instagram","Example","https://www.instagram.com/ss_demo/","", "E1","ss_demo",
                      {"username":"ss_demo"},kind="public_profile")
        self.case.coverage=[dict(source="instagram",status="ok",detail="retrieved"),
                            dict(source="facebook",status="not_found",detail="HTTP 404"),
                            dict(source="reddit",status="blocked",detail="HTTP 403"),
                            dict(source="x",status="skipped",detail="not checked")]
        write_report(self.case)
        text=(self.case.directory/"accounts.txt").read_text(encoding="utf-8")
        self.assertIn("Instagram: ss_demo",text)
        self.assertIn("Facebook: X",text)
        self.assertIn("Reddit: ?",text)
        self.assertIn("X: ?",text)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(verify(self.case.directory),0)

    def test_name_search_and_browser_account_candidates_are_marked(self):
        self.case.add("browser_google","A profile","https://www.instagram.com/other_handle/","", "E1","Example Researcher",kind="web_search_lead")
        self.case.add("bluesky","Example","https://bsky.app/profile/example.bsky.social","", "E2","Example Researcher",
                      {"username":"example.bsky.social","match_type":"search candidate"},kind="public_profile")
        text=account_lines(self.case.data())
        self.assertIn("Instagram: other_handle [candidate",text)
        self.assertIn("Bluesky: example.bsky.social [candidate",text)

    def test_post_urls_are_not_converted_to_fake_account_handles(self):
        for value in ("https://www.instagram.com/p/123","https://www.youtube.com/watch?v=abc","https://www.facebook.com/profile.php?id=12"):
            self.assertIsNone(account_from_url(value))

    def test_results_command_exports_old_case_and_preserves_original_hashes(self):
        self.case.add("instagram","Example","https://www.instagram.com/ss_demo/","Public bio", "E1","ss_demo",
                      {"username":"ss_demo"},kind="public_profile")
        write_report(self.case)
        before=json.loads((self.case.directory/"manifest.json").read_text())
        output=io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["results",str(self.case.directory),"--export-accounts"]),0)
            self.assertEqual(verify(self.case.directory),0)
        self.assertIn("Username: ss_demo",output.getvalue())
        self.assertIn("https://www.instagram.com/ss_demo/",output.getvalue())
        after=json.loads((self.case.directory/"manifest.json").read_text())
        self.assertEqual(before,after)

    def test_accounts_export_does_not_hide_tampered_evidence(self):
        self.case.record("test","https://example.org",200,b"original",0)
        write_report(self.case)
        (self.case.evidence_dir/"E00001.bin").write_bytes(b"modified")
        write_accounts(self.case.data(),self.case.directory,update_manifest=True)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(verify(self.case.directory),1)

    def test_scan_last_output_is_the_account_file_path(self):
        output=io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["scan","--username","ss_demo","--offline","--sources","instagram","--out",str(self.root/"scan")]),0)
        self.assertTrue(output.getvalue().splitlines()[-1].startswith("Accounts text file: "))
        self.assertTrue((self.root/"scan"/"accounts.txt").is_file())

    def test_offline_browser_option_never_launches_a_browser(self):
        with patch("q4r.cli.collect_browser",side_effect=AssertionError("browser launched")),redirect_stdout(io.StringIO()):
            self.assertEqual(main(["scan","--username","ss_demo","--offline","--sources","browser","--out",str(self.root/"offline-browser")]),0)

    def test_interactive_browser_flag_survives_target_prompts(self):
        factory=lambda target,directory: Case(target,self.root/"interactive")
        with patch("q4r.cli.Case",side_effect=factory),patch.dict("q4r.cli.PROVIDERS",{"fixture":lambda *args:("ok","empty fixture")},clear=True), \
             patch.dict("os.environ",{"BRAVE_SEARCH_API_KEY":""}),patch("builtins.input",side_effect=["Example Researcher","ss_demo",""]), \
             patch("q4r.cli.collect_browser",return_value=("ok","fixture browser complete")) as browser,redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--browser"]),0)
        browser.assert_called_once()
        self.assertTrue((self.root/"interactive"/"accounts.txt").is_file())


if __name__ == "__main__":
    unittest.main()
