"""Real Firefox smoke test using intercepted offline search-page fixtures only."""
import base64
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlencode

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from q4r.browser import search_pages
from q4r.core import Case,Target
from q4r.report import write_report
from q4r.cli import verify


with tempfile.TemporaryDirectory(prefix="q4r-browser-check-") as tmp:
    case=Case(Target(name="Example Researcher",username="example_handle"),Path(tmp)/"case")
    case.plan=[{"query":'"Example Researcher"',"url":"https://www.google.com/search?q=example","status":"manual_search_link"}]
    target="https://www.instagram.com/example_handle/"
    b64=base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    fixtures={
        "www.google.com":f'<html><title>Search</title><body><div><div><a href="{target}"><h3>Example profile</h3></a></div><p>Public snippet</p></div></body></html>',
        "www.bing.com":f'<html><title>Search</title><body><li class="b_algo"><h2><a href="https://www.bing.com/ck/a?u=a1{b64}">Example profile</a></h2><p>Public snippet</p></li></body></html>',
        "duckduckgo.com":f'<html><title>Search</title><body><article data-testid="result"><h2><a data-testid="result-title-a" href="{target}">Example profile</a></h2><p>Public snippet</p></article></body></html>',
    }
    from urllib.parse import urlsplit
    with sync_playwright() as pw:
        browser=pw.firefox.launch(headless=True)
        context=browser.new_context()
        def route(request):
            host=urlsplit(request.request.url).hostname
            if host in fixtures:
                request.fulfill(status=200,content_type="text/html",body=fixtures[host])
            else:
                request.abort()
        context.route("**/*",route)
        status,detail=search_pages(case,context,["google","bing","duckduckgo"],3,5,10,interval=0)
        context.close()
        browser.close()
    assert status=="ok",detail
    assert len(case.findings)==3
    assert all(f.url==target for f in case.findings)
    assert {f.source for f in case.findings}=={"browser_google","browser_bing","browser_duckduckgo"}
    write_report(case)
    assert verify(case.directory)==0
    assert "Instagram: example_handle [candidate" in (case.directory/"accounts.txt").read_text(encoding="utf-8")
    print("PASS: real Firefox extracted all three offline engine fixtures; evidence and account export verified.")
