import argparse
import hashlib
import json
import os
import shlex
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import __version__
from .core import Case, Target, build_plan, load_sites, safe_url
from .http import Client, SourceError
from .providers import PROVIDERS, brave, wayback
from .social import SOCIAL_PROVIDERS
from .report import write_report
from .results import print_findings, write_accounts
from .browser import ENGINES, collect_browser, install_browser
from .update import automatic_update, check_update


def bounded_int(low, high):
    def parse(value):
        try:
            n = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("Expected an integer") from None
        if not low <= n <= high:
            raise argparse.ArgumentTypeError(f"Expected {low}..{high}")
        return n
    return parse


def verify(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    failures = []
    for name, digest in manifest.items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory) or not path.is_file():
            failures.append(name)
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            failures.append(name)
    print(f"Checked {len(manifest)} files; {len(failures)} missing/modified")
    for name in failures:
        print("FAILED:", name)
    return 1 if failures else 0


def show_queries(directory, limit):
    """Read an old or new case locally; never contact search engines."""
    path = Path(directory)
    report = path if path.is_file() else path / "report.json"
    data = json.loads(report.read_text(encoding="utf-8"))
    plan = data.get("plan", [])
    if not isinstance(plan, list):
        raise ValueError("Invalid search plan")
    displayed = 0
    for item in plan:
        if not isinstance(item, dict):
            continue
        url = safe_url(item.get("url", ""))
        if not url:
            continue
        # Print only validated URLs. Avoid terminal control sequences in old case text.
        print(url)
        displayed += 1
        if displayed >= limit:
            break
    print(f"{displayed} manual search links displayed; opening them runs a search in your browser.")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(prog="q4r", description="Q4R — public-source research with auditable evidence")
    p.add_argument("--version", action="version", version="Q4R " + __version__)
    p.add_argument("--no-update-check", action="store_true", help="Skip the interactive startup update check")
    p.add_argument("--browser", dest="interactive_browser", action="store_true", help="Enable visible browser search with interactive target prompts")
    sub = p.add_subparsers(dest="command")
    scan = sub.add_parser("scan", help="Collect public leads and export a report")
    for flag in ("name", "username", "email"):
        scan.add_argument("--" + flag, default="")
    scan.add_argument("--country", default="", help="Optional country keyword for web searches; not a verified location filter")
    scan.add_argument("--alias", action="append", default=[], help="Additional known username of the same subject; repeat up to five times")
    scan.add_argument("--out", type=Path, help="New case directory (never overwrites existing cases)")
    scan.add_argument("--offline", action="store_true", help="Build search plan only; no network requests")
    scan.add_argument("--no-update-check", action="store_true", default=argparse.SUPPRESS, help="Skip the interactive startup update check")
    scan.add_argument("--require-web", action="store_true", help="Fail before collection if automatic Brave web search is not configured")
    scan.add_argument("--sources", default=",".join(PROVIDERS) + ",brave", help="Comma-separated sources; q4r sources lists them")
    scan.add_argument("--workers", type=bounded_int(1, 8), default=4, help="Parallel sources (default 4); each host remains rate-limited")
    scan.add_argument("--limit", type=bounded_int(1, 20), default=5, help="Results per source/query (default 5)")
    scan.add_argument("--web-budget", type=bounded_int(1, 500), default=12, help="Max Brave queries; may consume API credits (default 12)")
    scan.add_argument("--timeout", type=bounded_int(1, 60), default=15)
    scan.add_argument("--wayback", action="store_true", help="Look up up to 10 retrieved URLs in Wayback")
    scan.add_argument("--sites", type=Path, help="Alternative local JSON registry for site-specific search queries")
    scan.add_argument("--ai-model", help="Opt-in installed local Ollama model; sends up to 40 evidence snippets to localhost")
    scan.add_argument("--browser", action="store_true", help="Search visible web engine results in an isolated browser (optional extra)")
    scan.add_argument("--search-engines", default="google,bing,duckduckgo", help="Comma-separated browser engines")
    scan.add_argument("--browser-budget", type=bounded_int(1,100), default=6, help="Maximum browser search pages total (default 6)")
    scan.add_argument("--browser-type", choices=("firefox","chromium"), default="firefox")
    scan.add_argument("--browser-headless", action="store_true")
    scan.add_argument("--browser-manual", action="store_true", help="Pause for you to review each visible search page before reading it")
    scan.add_argument("--preview-limit", type=bounded_int(1,100), default=20, help="Maximum retrieved leads displayed in this terminal")
    sub.add_parser("sources", help="Show live connectors and search-plan site registry")
    check = sub.add_parser("verify", help="Check report/evidence files against manifest hashes")
    check.add_argument("directory", type=Path)
    queries = sub.add_parser("queries", help="Print manual search links from an existing case (including v0.1 cases)")
    queries.add_argument("directory", type=Path, help="Case directory or report.json path")
    queries.add_argument("--limit", type=bounded_int(1, 2000), default=20)
    results = sub.add_parser("results", help="Show saved findings from an old/new report without searching again")
    results.add_argument("directory", type=Path)
    results.add_argument("--limit", type=bounded_int(1,100), default=20)
    results.add_argument("--export-accounts", action="store_true", help="Create accounts.txt and add only its hash to an existing manifest")
    installer = sub.add_parser("browser-install", help="Install the optional Playwright browser runtime")
    installer.add_argument("--browser-type", choices=("firefox","chromium"), default="firefox")
    installer.add_argument("--with-deps", action="store_true", help="Also run Playwright's system dependency installer")
    updater = sub.add_parser("update", help="Check the official repository and offer an automatic update")
    updater.add_argument("--check", action="store_true", help="Only display update availability; do not install")
    args = p.parse_args(argv)
    if args.command == "update":
        return check_update([], explicit=True, check_only=args.check)
    if args.command in (None, "scan"):
        update_status = automatic_update(argv, args.no_update_check, getattr(args, "offline", False))
        if update_status is not None:
            return update_status
    if args.command == "sources":
        print("Built-in source connectors: " + ", ".join(PROVIDERS) + ", brave (API key), wayback (opt-in)")
        print("Social profile checks without a Brave key: " + ", ".join(SOCIAL_PROVIDERS))
        print("Use --sources social for these only; some platforms may return blocked or unconfirmed.")
        print("Optional browser engine collection: --browser or --sources browser (requires browser extra/runtime)")
        print("Search registry (queries only; not automatic profile detectors):")
        for site in load_sites():
            print("  " + site["domain"] + " [" + site["category"] + "]")
        return 0
    if args.command == "browser-install":
        return install_browser(args.browser_type,args.with_deps)
    if args.command == "results":
        try:
            report = args.directory if args.directory.is_file() else args.directory / "report.json"
            data = json.loads(report.read_text(encoding="utf-8"))
            print_findings(data,args.limit)
            if args.export_accounts:
                path = write_accounts(data,report.parent,update_manifest=True)
                print("Accounts text file: " + str(path))
            return 0
        except (OSError,ValueError,TypeError,AttributeError) as exc:
            print("Cannot read saved results: " + str(exc),file=sys.stderr)
            return 2
    if args.command == "verify":
        try:
            return verify(args.directory)
        except (OSError, ValueError, TypeError) as exc:
            print("Verify failed: " + str(exc), file=sys.stderr)
            return 2
    if args.command == "queries":
        try:
            return show_queries(args.directory, args.limit)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            print("Cannot read search plan: " + str(exc), file=sys.stderr)
            return 2
    if args.command is None:
        print(f"Q4R {__version__} — public-source research / بحث المصادر العامة")
        print("Enter an authorized subject. Leave optional fields blank. / الحقول اختيارية")
        try:
            use_browser = args.interactive_browser
            values = ["scan", "--name", input("Full name / الاسم (2+ parts): "),
                      "--username", input("Username / اسم المستخدم: "),
                      "--email", input("Email / البريد (optional): "),
                      "--country", input("Country / البلد (optional): ")]
            aliases = input("Other known usernames of the SAME subject, comma-separated / يوزرات إضافية لنفس الشخص (optional): ")
            for value in aliases.replace("،", ",").split(","):
                if value.strip():
                    values.extend(["--alias", value.strip()])
            args = p.parse_args(values + (["--browser"] if use_browser else []))
        except EOFError:
            p.print_help()
            return 2
    try:
        args.browser = args.browser or args.interactive_browser
        target = Target(args.name, args.username, args.email, args.country, args.alias)
        sites = load_sites(args.sites)
        selected = []
        for source in args.sources.split(","):
            source = source.strip()
            selected.extend(SOCIAL_PROVIDERS if source == "social" else list(PROVIDERS) + ["brave"] if source == "all" else [source])
        selected = list(dict.fromkeys(selected))
        if "browser" in selected:
            args.browser = True
            selected.remove("browser")
        engines = list(dict.fromkeys(x.strip() for x in args.search_engines.split(",")))
        if set(engines) - set(ENGINES):
            raise ValueError("Unknown browser search engine; use google,bing,duckduckgo")
        if args.browser_headless and args.browser_manual:
            raise ValueError("--browser-manual needs a visible browser; omit --browser-headless")
        unknown = set(selected) - (set(PROVIDERS) | {"brave"})
        if unknown:
            raise ValueError("Unknown sources: " + ", ".join(sorted(unknown)))
        web_ready = bool(os.getenv("BRAVE_SEARCH_API_KEY", "").strip())
        if args.require_web and (args.offline or not (args.browser or ("brave" in selected and web_ready))):
            raise ValueError("--require-web needs the brave source, online mode and BRAVE_SEARCH_API_KEY. "
                             "Alternatively enable --browser. No collection was started.")
        if "brave" in selected and not args.offline and not web_ready:
            print("[Q4R] LIMITED COVERAGE: BRAVE_SEARCH_API_KEY is missing.\n"
                  "  Brave's general web index will NOT run. Selected direct social/profile connectors still run without it.\n"
                  "  Manual web search links will be saved; blocked/skipped sources are not evidence of absent accounts.", flush=True)
        if args.browser and not args.offline:
            print("[Q4R] Browser search enabled: " + ", ".join(engines) + "; no Brave key required.",flush=True)
        from datetime import datetime, timezone
        from uuid import uuid4
        directory = args.out or Path("cases") / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6])
        case = Case(target, directory)
    except (ValueError, OSError) as exc:
        p.error(str(exc))
    case.plan = list(build_plan(target, sites))
    client = Client(case, timeout=args.timeout)
    interrupted = False
    def invoke(source, view):
        try:
            if source == "brave":
                return brave(view, client, args.limit, args.web_budget)
            if source == "wayback":
                return wayback(view, client, min(args.limit, 10))
            return PROVIDERS[source](view, client, args.limit)
        except (SourceError, KeyError, TypeError, ValueError, AttributeError) as exc:
            status = "blocked" if isinstance(exc, SourceError) and exc.status in (401, 403, 429) else "error"
            detail = str(exc) if isinstance(exc, SourceError) else "Unexpected source schema: " + type(exc).__name__
            case.check(source, status, detail, evidence_id=getattr(exc, "evidence_id", ""), query=view.target.username)
            return status, detail

    def run_source(source):
        lookups = []
        with case.lock:
            before = sum(f.source == source for f in case.findings)
        if args.offline:
            status, detail = "skipped", "offline mode: no requests sent"
        else:
            if source in set(SOCIAL_PROVIDERS) | {"github", "gitlab", "keybase"}:
                for index, username in enumerate(target.usernames() or [""]):
                    state, message = invoke(source, case.for_username(username, include_name=index == 0))
                    lookups.append(dict(username=username, status=state, detail=message))
                status, detail = lookups[0]["status"], lookups[0]["detail"]
                if len(lookups) > 1:
                    states = {row["status"] for row in lookups}
                    status = "ok" if "ok" in states else "not_found" if states == {"not_found"} else "skipped"
                    for state in ("unconfirmed", "blocked", "error", "partial"):
                        if state in states:
                            status = "partial" if "ok" in states else state
                    if states == {"not_found", "skipped"}:
                        status = "unconfirmed"
                    detail = "; ".join((row["username"] or "name search") + ": " + row["status"] for row in lookups)
            else:
                status, detail = invoke(source, case)
        with case.lock:
            return dict(source=source, status=status, detail=detail,
                        lookups=lookups,
                        new_leads=sum(f.source == source for f in case.findings) - before)

    def completed(result):
        with case.lock:
            case.coverage.append(result)
        print(f'[Q4R] {result["source"]}: {result["status"]}: {result["detail"]}; {result["new_leads"]} new leads', flush=True)
        write_report(case)

    try:
        print(f"[Q4R] Starting {len(selected)} sources; workers={args.workers}", flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(run_source, source) for source in selected]
            try:
                for future in as_completed(futures):
                    completed(future.result())
            except KeyboardInterrupt:
                client.cancelled.set()
                for future in futures:
                    future.cancel()
                raise
        # Archive lookups depend on already-collected URLs and run only after source workers finish.
        if args.browser:
            before = len(case.findings)
            if args.offline:
                state, detail = "skipped", "offline mode: browser was not launched"
            else:
                state, detail = collect_browser(case,engines,args.browser_budget,args.limit,max(args.timeout,15),
                                               args.browser_type,args.browser_headless,args.browser_manual)
            completed(dict(source="browser",status=state,detail=detail,new_leads=len(case.findings)-before))
        if args.wayback:
            completed(run_source("wayback"))
        if args.ai_model and not args.offline:
            try:
                from .ai import summarize
                summarize(case, args.ai_model)
                case.coverage.append(dict(source="ollama", status="ok", detail="AI draft saved separately; human review required"))
            except Exception as exc:
                case.coverage.append(dict(source="ollama", status="error", detail="Local summary failed: " + type(exc).__name__))
    except KeyboardInterrupt:
        interrupted = True
        case.coverage.append(dict(source="run", status="interrupted", detail="User interrupted; partial results saved"))
    finally:
        write_report(case)
    print(f"\n{len(case.findings)} leads; report: {case.directory.resolve() / 'report.html'}")
    print_findings(case.data(),args.preview_limit)
    print("Report URI: " + (case.directory.resolve() / "report.html").as_uri())
    if not case.findings:
        print("No leads were retrieved from the sources that ran. This does not prove absence of public accounts.")
    web_coverage = next((c for c in case.coverage if c["source"] == "brave"), None)
    if web_coverage and web_coverage["status"] == "skipped" and not args.offline:
        print("Brave indexed web search DID NOT RUN: " + web_coverage["detail"])
    if case.plan:
        print("Manual search links: " + str(case.directory.resolve() / "searches.txt"))
        print("Show links in this terminal: q4r queries " + shlex.quote(str(case.directory.resolve())) + " --limit 20")
        if not case.findings:
            for entry in case.plan[:3]:
                print("  " + entry["url"])
    print("Identity is unverified. Search links are planned tasks. Keep case files private.")
    print("Accounts text file: " + str(case.directory.resolve() / "accounts.txt"))
    if interrupted:
        return 130
    return 3 if any(c["status"] in ("error", "blocked", "partial") for c in case.coverage) else 0
