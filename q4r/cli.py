import argparse
import hashlib
import json
import os
import shlex
import sys
from pathlib import Path

from . import __version__
from .core import Case, Target, build_plan, load_sites, safe_url
from .http import Client, SourceError
from .providers import PROVIDERS, brave, wayback
from .report import write_report


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
    p = argparse.ArgumentParser(prog="q4r", description="Q4R — public-source research with auditable evidence")
    p.add_argument("--version", action="version", version="Q4R " + __version__)
    sub = p.add_subparsers(dest="command")
    scan = sub.add_parser("scan", help="Collect public leads and export a report")
    for flag in ("name", "username", "email"):
        scan.add_argument("--" + flag, default="")
    scan.add_argument("--out", type=Path, help="New case directory (never overwrites existing cases)")
    scan.add_argument("--offline", action="store_true", help="Build search plan only; no network requests")
    scan.add_argument("--require-web", action="store_true", help="Fail before collection if automatic Brave web search is not configured")
    scan.add_argument("--sources", default=",".join(PROVIDERS) + ",brave", help="Comma-separated sources; q4r sources lists them")
    scan.add_argument("--limit", type=bounded_int(1, 20), default=5, help="Results per source/query (default 5)")
    scan.add_argument("--web-budget", type=bounded_int(1, 500), default=12, help="Max Brave queries; may consume API credits (default 12)")
    scan.add_argument("--timeout", type=bounded_int(1, 60), default=15)
    scan.add_argument("--wayback", action="store_true", help="Look up up to 10 retrieved URLs in Wayback")
    scan.add_argument("--sites", type=Path, help="Alternative local JSON registry for site-specific search queries")
    scan.add_argument("--ai-model", help="Opt-in installed local Ollama model; sends up to 40 evidence snippets to localhost")
    sub.add_parser("sources", help="Show live connectors and search-plan site registry")
    check = sub.add_parser("verify", help="Check report/evidence files against manifest hashes")
    check.add_argument("directory", type=Path)
    queries = sub.add_parser("queries", help="Print manual search links from an existing case (including v0.1 cases)")
    queries.add_argument("directory", type=Path, help="Case directory or report.json path")
    queries.add_argument("--limit", type=bounded_int(1, 2000), default=20)
    args = p.parse_args(argv)
    if args.command == "sources":
        print("Live JSON connectors: " + ", ".join(PROVIDERS) + ", brave (API key), wayback (opt-in)")
        print("Search registry (queries only; not automatic profile detectors):")
        for site in load_sites():
            print("  " + site["domain"] + " [" + site["category"] + "]")
        return 0
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
            args = p.parse_args(["scan", "--name", input("Full name / الاسم (2+ parts): "),
                                "--username", input("Username / اسم المستخدم: "),
                                "--email", input("Email / البريد (optional): ")])
        except EOFError:
            p.print_help()
            return 2
    try:
        target = Target(args.name, args.username, args.email)
        sites = load_sites(args.sites)
        selected = list(dict.fromkeys(args.sources.split(",")))
        unknown = set(selected) - (set(PROVIDERS) | {"brave"})
        if unknown:
            raise ValueError("Unknown sources: " + ", ".join(sorted(unknown)))
        web_ready = bool(os.getenv("BRAVE_SEARCH_API_KEY", "").strip())
        if args.require_web and (args.offline or "brave" not in selected or not web_ready):
            raise ValueError("--require-web needs the brave source, online mode and BRAVE_SEARCH_API_KEY. "
                             "No collection was started; configure the API key and retry.")
        if "brave" in selected and not args.offline and not web_ready:
            print("[Q4R] LIMITED COVERAGE: BRAVE_SEARCH_API_KEY is missing.\n"
                  "  Automatic web/social search will NOT run. Only the selected public API lookups will run.\n"
                  "  Manual search links will be saved; skipped searches are not evidence of absent accounts.", flush=True)
        from datetime import datetime, timezone
        from uuid import uuid4
        directory = args.out or Path("cases") / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6])
        case = Case(target, directory)
    except (ValueError, OSError) as exc:
        p.error(str(exc))
    case.plan = list(build_plan(target, sites))
    client = Client(case, timeout=args.timeout)
    interrupted = False
    try:
        for source in selected + (["wayback"] if args.wayback else []):
            print("[Q4R] " + source, flush=True)
            before = len(case.findings)
            if args.offline:
                status, detail = "skipped", "offline mode: no requests sent"
            else:
                try:
                    if source == "brave":
                        status, detail = brave(case, client, args.limit, args.web_budget)
                    elif source == "wayback":
                        status, detail = wayback(case, client, min(args.limit, 10))
                    else:
                        status, detail = PROVIDERS[source](case, client, args.limit)
                except (SourceError, KeyError, TypeError, ValueError, AttributeError) as exc:
                    status = "error"
                    detail = str(exc) if isinstance(exc, SourceError) else "Unexpected source schema: " + type(exc).__name__
            case.coverage.append(dict(source=source, status=status, detail=detail, new_leads=len(case.findings) - before))
            print("  " + status + ": " + detail + f"; {len(case.findings) - before} new leads", flush=True)
            write_report(case)  # A failed later provider never discards earlier results.
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
    print("Report URI: " + (case.directory.resolve() / "report.html").as_uri())
    if not case.findings:
        print("No leads were retrieved from the sources that ran. This does not prove absence of public accounts.")
    web_coverage = next((c for c in case.coverage if c["source"] == "brave"), None)
    if web_coverage and web_coverage["status"] == "skipped" and not args.offline:
        print("Automatic web/social search DID NOT RUN: " + web_coverage["detail"])
    if case.plan:
        print("Manual search links: " + str(case.directory.resolve() / "searches.txt"))
        print("Show links in this terminal: q4r queries " + shlex.quote(str(case.directory.resolve())) + " --limit 20")
        if not case.findings:
            for entry in case.plan[:3]:
                print("  " + entry["url"])
    print("Identity is unverified. Search links are planned tasks. Keep case files private.")
    if interrupted:
        return 130
    return 3 if any(c["status"] == "error" for c in case.coverage) else 0
