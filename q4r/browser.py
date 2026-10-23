"""Optional visible-browser search. Collect rendered result links, never bypass gates."""
import base64
import ipaddress
import json
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from .core import public_text, safe_url


ENGINES = {
    "google": {"base":"https://www.google.com/search", "hosts":{"google.com","www.google.com"},
               "selector":"a:has(h3)", "container":"xpath=../../.."},
    "bing": {"base":"https://www.bing.com/search", "hosts":{"bing.com","www.bing.com"},
             "selector":"li.b_algo h2 a", "container":"xpath=ancestor::li[1]"},
    "duckduckgo": {"base":"https://duckduckgo.com/", "hosts":{"duckduckgo.com","www.duckduckgo.com"},
                   "selector":"a[data-testid='result-title-a'], a.result__a", "container":"xpath=ancestor::article[1]"},
}


def engine_url(engine, query):
    return ENGINES[engine]["base"] + "?" + urlencode({"q":query})


def result_url(href, engine):
    """Decode known search wrappers without requesting any destination."""
    value = urljoin(ENGINES[engine]["base"], str(href))
    try:
        p = urlsplit(value)
        params = parse_qs(p.query)
        if p.hostname in ENGINES[engine]["hosts"]:
            if engine == "google" and p.path == "/url":
                value = (params.get("q") or params.get("url") or [""])[0]
            elif engine == "duckduckgo" and p.path.startswith("/l/"):
                value = params.get("uddg", [""])[0]
            elif engine == "bing" and p.path == "/ck/a":
                encoded = params.get("u", [""])[0]
                if not encoded.startswith("a1"):
                    return ""
                encoded = encoded[2:]
                value = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
            else:
                return ""
        value = safe_url(value)
        host = urlsplit(value).hostname if value else ""
        if not host or "." not in host or host.endswith((".localhost", ".local", ".internal", ".lan")):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            pass
        return value
    except (ValueError, UnicodeError):
        return ""


def page_state(engine, url, title, text, status, count):
    p = urlsplit(url)
    if p.hostname not in ENGINES[engine]["hosts"]:
        return "blocked", "Navigation left the selected search engine (possibly login/consent)"
    text = (title + "\n" + text[:8000]).casefold()
    gates = ("unusual traffic", "verify you are human", "just a moment", "complete the captcha", "not a robot",
             "verify you're human", "access denied", "before you continue to google", "one last step")
    if status in (401,403,429) or "/sorry/" in p.path or any(x in text for x in gates):
        return "blocked", "Search engine returned a login, consent, rate-limit or human-verification gate"
    if status and status >= 400:
        return "error", f"Search page HTTP {status}"
    if count:
        return "found", f"{count} visible indexed search leads; identity/ownership unverified"
    if any(x in text for x in ("did not match any documents", "there are no results", "no results found", "لم يتم العثور على نتائج")):
        return "not_found", "Search engine explicitly returned no indexed results for this query"
    return "unconfirmed", "No recognized visible results; page layout, loading or access may differ"


def extract_visible(page, engine, limit):
    rows, seen = [], set()
    for anchor in page.locator(ENGINES[engine]["selector"]).all()[:max(limit * 3,20)]:
        if not anchor.is_visible():
            continue
        destination = result_url(anchor.get_attribute("href") or "", engine)
        if not destination or destination in seen:
            continue
        seen.add(destination)
        if engine == "google":
            title = anchor.locator("h3").first.inner_text(timeout=2000)
        else:
            title = anchor.inner_text(timeout=2000)
        if not title.strip():
            continue
        snippet = ""
        try:
            container = anchor.locator(ENGINES[engine]["container"])
            if container.count():
                snippet = container.first.inner_text(timeout=2000)[:1200]
        except Exception:
            pass
        rows.append(dict(title=public_text(title)[:1000],url=destination,snippet=public_text(snippet)))
        if len(rows) >= limit:
            break
    return rows


def search_pages(case, context, engines, budget, limit, timeout, interactive=False, interval=2):
    """Browser context is injectable so the driver is tested against offline page fixtures."""
    page, attempts, blocked, statuses = context.new_page(), 0, set(), []
    try:
        for query_entry in case.plan:
            if attempts >= budget:
                break
            query = query_entry["query"]
            if len(query) > 600:
                continue
            for engine in engines:
                if attempts >= budget:
                    break
                source = "browser_" + engine
                if engine in blocked:
                    continue
                if attempts and interval:
                    page.wait_for_timeout(interval * 1000)
                endpoint, started = engine_url(engine,query), time.monotonic()
                print(f"[Q4R] Browser {engine}: {public_text(query)}", flush=True)
                attempts += 1
                rows, title, body, current_url, status = [], "", "", endpoint, 0
                try:
                    response = page.goto(endpoint, wait_until="domcontentloaded", timeout=timeout*1000)
                    status = response.status if response else 0
                    try:
                        page.locator(ENGINES[engine]["selector"]).first.wait_for(state="visible",timeout=min(timeout*1000,5000))
                    except Exception:
                        pass
                    title, current_url = page.title(), page.url
                    body = page.locator("body").inner_text(timeout=3000)[:12000]
                    rows = extract_visible(page,engine,limit)
                    state, detail = page_state(engine,current_url,title,body,status,len(rows))
                    if interactive and sys.stdin.isatty():
                        print("Review this search page in the browser. Q4R will not solve challenges or click login/consent controls.")
                        input("Press Enter to read the currently visible results, or Ctrl+C to stop: ")
                        title, current_url = page.title(), page.url
                        body = page.locator("body").inner_text(timeout=3000)[:12000]
                        rows = extract_visible(page,engine,limit)
                        state, detail = page_state(engine,current_url,title,body,status,len(rows))
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    state, detail = "error", "Browser navigation/extraction failed: " + type(exc).__name__
                if state == "blocked":
                    blocked.add(engine)  # Do not keep hammering a challenged engine.
                if state != "found":
                    rows = []
                observation = dict(engine=engine,query=query,requested_url=endpoint,page_url=safe_url(current_url),
                                   title=public_text(title),visible_text=public_text(body),results=rows,status=state,detail=detail)
                eid = case.record(source,endpoint,status,json.dumps(observation,ensure_ascii=False).encode("utf-8"),
                                  time.monotonic()-started,error=detail if state in ("error","blocked") else "",
                                  evidence_type="browser_rendered_observation")
                case.check(source,state,detail,endpoint,eid,query)
                statuses.append(state)
                for row in rows:
                    case.add(source,row["title"],row["url"],row["snippet"],eid,query,
                             {"search_engine":engine,"match_type":"indexed web search candidate; not a verified profile"},kind="web_search_lead")
        if not attempts:
            return "skipped", "No eligible browser queries in the plan"
        failed = any(s in ("error","blocked") for s in statuses)
        state = "partial" if failed and "found" in statuses else "blocked" if "blocked" in statuses else "error" if "error" in statuses else "ok"
        return state, f"{attempts} browser search pages inspected; results remain unverified index leads"
    finally:
        try:
            page.close()
        except Exception:
            pass


def collect_browser(case, engines, budget=6, limit=5, timeout=30, browser="firefox", headless=False, interactive=False):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "error", "Browser extra missing. Install with: pipx inject q4r-osint playwright; then q4r browser-install"
    try:
        with sync_playwright() as playwright:
            options = {"headless":headless}
            if browser == "chromium":
                options["chromium_sandbox"] = True
            instance = getattr(playwright,browser).launch(**options)
            try:
                context = instance.new_context(accept_downloads=False)
                try:
                    return search_pages(case,context,engines,budget,limit,timeout,interactive)
                finally:
                    context.close()
            finally:
                instance.close()
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        return "error", "Browser could not run: " + type(exc).__name__ + ". Run q4r browser-install; check GUI/system dependencies. No sandbox/security bypass flags are used."


def install_browser(browser="firefox", with_deps=False):
    try:
        import playwright
    except ImportError:
        print("Install the optional browser extra first: pipx inject q4r-osint playwright",file=sys.stderr)
        return 2
    command = [sys.executable,"-m","playwright","install"]
    if with_deps:
        command.append("--with-deps")
    command.append(browser)
    return subprocess.call(command)
