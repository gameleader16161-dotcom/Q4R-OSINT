"""Shared local application engine for Windows and Android; no HTTP server."""
import base64
from contextlib import redirect_stdout
import copy
from datetime import datetime, timezone
import io
import json
import math
import os
from pathlib import Path
import re
import threading
from uuid import uuid4
import zipfile

from . import __version__
from .cli import main as cli_main
from .core import Target, safe_url
from .photos import attach_photo, decode_photo, clean_reference_png
from .providers import PROVIDERS
from .results import email_summary_line


def ranked_report(data):
    data = copy.deepcopy(data)
    for finding in data.get("findings", []):
        try:
            score = float(finding.get("score", 0))
        except (ValueError, TypeError):
            score = 0
        score = min(90, max(0, score)) if math.isfinite(score) else 0
        finding["score"] = score
        finding["match_percent"] = round(score * 100 / 90)
    data["findings"] = sorted(data.get("findings", []), key=lambda row: row["score"], reverse=True)
    data["score_interpretation"] = "Normalized text evidence score, not probability or proof of identity."
    data["email_summary"] = email_summary_line(data)
    coverage = [row for row in data.get("coverage", []) if row.get("source") != "run"]
    run = data.get("run", {})
    outcome = run.get("outcome", "saved")
    if any(row.get("status") == "interrupted" for row in data.get("coverage", [])):
        outcome = "cancelled"
    elif any(row.get("status") in ("error", "blocked", "partial", "unconfirmed") for row in coverage) and outcome in ("complete", "saved"):
        outcome = "partial"
    attempted = {"browser" if row.get("source", "").startswith("browser_") else row.get("source") for row in data.get("requests", [])}
    total = run.get("total_sources")
    if outcome in ("cancelled", "planned"):
        done = len(attempted)
    else:
        done = len(coverage)
        total = total if isinstance(total, int) else len(coverage)
    data["run_summary"] = dict(status=outcome, completed=done, total=total)
    return data


class LogSink(io.TextIOBase):
    encoding = "utf-8"
    def __init__(self, service):
        self.service = service

    def write(self, text):
        with self.service.lock:
            self.service.state["log"] = (self.service.state.get("log", "") + text)[-16000:]
        return len(text)


class AppService:
    def __init__(self, root, platform="desktop"):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.platform = platform
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.worker = None
        self.state = dict(status="idle", log="", completed=0, total=0, report=None, case_id=None)

    def case_path(self, case_id):
        if not isinstance(case_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", case_id):
            raise ValueError("Invalid case identifier")
        path = (self.root/case_id).resolve()
        if path.parent != self.root or not path.is_dir():
            raise ValueError("Case not found")
        return path

    def load_case(self, case_id, with_photo=True):
        directory = self.case_path(case_id)
        report = directory/"report.json"
        if report.resolve().parent != directory or report.stat().st_size > 12*1024*1024:
            raise ValueError("Report is outside the case or too large")
        data = ranked_report(json.loads(report.read_text(encoding="utf-8")))
        photo = None
        path = directory/"attachments/reference.png"
        if with_photo and path.is_file() and path.resolve().is_relative_to(directory) and path.stat().st_size <= 6*1024*1024:
            try:
                pixels, _, _ = clean_reference_png(path.read_bytes())
                photo = "data:image/png;base64," + base64.b64encode(pixels).decode("ascii")
            except ValueError:
                pass
        return dict(case_id=case_id, report=data, photo=photo)

    def list_cases(self):
        cases = []
        for path in self.root.iterdir():
            if not path.is_dir() or not (path/"report.json").is_file():
                continue
            try:
                report = self.load_case(path.name, with_photo=False)["report"]
                target = report.get("target", {})
                title = target.get("name") or target.get("username") or target.get("email") or path.name
                cases.append(dict(id=path.name, title=title, date=report.get("started_at", ""), leads=len(report.get("findings", []))))
            except (ValueError, OSError, TypeError, KeyError):
                continue
        return sorted(cases, key=lambda row: row["date"], reverse=True)[:100]

    def start(self, config):
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise ValueError("A search is already running")
            target = Target(name=config.get("name", ""), username=config.get("username", ""), email=config.get("email", ""),
                            country=config.get("country", ""), aliases=config.get("aliases", []),
                            name_variants=config.get("name_variants", []))
            selected = config.get("sources", list(PROVIDERS) + ["brave"])
            if not isinstance(selected, list) or not selected or len(selected) > 19 or any(x not in set(PROVIDERS)|{"brave"} for x in selected):
                raise ValueError("Select valid sources")
            selected = list(dict.fromkeys(selected))
            browser = bool(config.get("browser"))
            if browser and self.platform == "android":
                raise ValueError("Desktop browser collection is unavailable on Android")
            image = decode_photo(config.get("photo"))
            key = config.get("brave_key", "").strip()
            if len(key) > 512 or any(ord(char) < 32 for char in key):
                raise ValueError("Invalid Brave key")
            case_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
            argv = ["--no-update-check", "scan", "--name", target.name, "--username", target.username, "--email", target.email,
                    "--country", target.country, "--sources", ",".join(selected), "--out", str(self.root/case_id)]
            for value in target.aliases:
                argv.extend(["--alias", value])
            for value in target.name_variants:
                argv.extend(["--name-variant", value])
            for key, low, high, default in (("limit",1,20,5), ("web_budget",1,500,12), ("browser_budget",1,100,12)):
                value = int(config.get(key, default))
                if not low <= value <= high:
                    raise ValueError("Invalid " + key)
                argv.extend(["--"+key.replace("_", "-"), str(value)])
            if config.get("offline"):
                argv.append("--offline")
            if browser:
                argv.append("--browser")
            self.cancel.clear()
            self.state = dict(status="running", case_id=case_id, completed=0, total=len(selected)+(1 if browser else 0), report=None, log="")
            self.worker = threading.Thread(target=self._run, args=(argv, image, key), daemon=True, name="Q4R-search")
            self.worker.start()
            return dict(case_id=case_id)

    def _run(self, argv, image, key):
        def observe(event, value):
            if event == "case":
                if image:
                    attach_photo(value, image)
            else:
                with self.lock:
                    self.state["report"] = ranked_report(value)
                    self.state["completed"] = len([row for row in value.get("coverage", []) if row.get("source") != "run"])
        previous_key = os.environ.get("BRAVE_SEARCH_API_KEY")
        if key:
            os.environ["BRAVE_SEARCH_API_KEY"] = key
        try:
            with redirect_stdout(LogSink(self)):
                code = cli_main(argv, observer=observe, cancellation=self.cancel)
            with self.lock:
                self.state["status"] = self.state["report"]["run_summary"]["status"] if code in (0,3,130) and self.state["report"] else "error"
                if self.state["report"]:
                    summary = self.state["report"]["run_summary"]
                    self.state["completed"], self.state["total"] = summary["completed"], summary["total"]
                self.state["exit_code"] = code
        except (Exception, SystemExit) as exc:
            with self.lock:
                self.state.update(status="error", error=str(exc))
        finally:
            if key:
                if previous_key is None:
                    os.environ.pop("BRAVE_SEARCH_API_KEY", None)
                else:
                    os.environ["BRAVE_SEARCH_API_KEY"] = previous_key

    def export_case(self, case_id):
        directory = self.case_path(case_id)
        exports = self.root.parent/"exports"
        exports.mkdir(exist_ok=True)
        destination = exports/(case_id+".zip")
        total = 0
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in directory.rglob("*"):
                if not path.is_file() or not path.resolve().is_relative_to(directory):
                    continue
                relative = path.relative_to(directory).as_posix()
                if "/" in relative and not relative.startswith(("attachments/", "evidence/")):
                    continue
                if "/" not in relative and path.name not in {"report.json", "report.html", "accounts.txt", "findings.csv", "requests.jsonl", "searches.txt", "graph.graphml", "manifest.json"}:
                    continue
                total += path.stat().st_size
                if total > 250*1024*1024:
                    raise ValueError("Case exceeds the 250 MB export limit")
                archive.write(path, relative)
        return str(destination)

    def handle(self, action, payload=None):
        payload = payload or {}
        if action == "bootstrap":
            return dict(version=__version__, platform=self.platform, sources=list(PROVIDERS)+["brave"], cases=self.list_cases(),
                        browser_available=self.platform != "android")
        if action == "start":
            return self.start(payload)
        if action == "status":
            with self.lock:
                return copy.deepcopy(self.state)
        if action == "cancel":
            self.cancel.set()
            with self.lock:
                if self.state.get("status") == "running":
                    self.state["status"] = "stopping"
            return dict(status="stopping")
        if action == "cases":
            return self.list_cases()
        if action == "load_case":
            return self.load_case(payload.get("case_id"))
        if action == "report_path":
            return str(self.case_path(payload.get("case_id"))/"report.html")
        if action == "export":
            return self.export_case(payload.get("case_id"))
        if action == "open_url":
            url = safe_url(payload.get("url", ""))
            if not url:
                raise ValueError("Only public HTTP(S) links can be opened")
            return url
        raise ValueError("Unknown application action")

    def request(self, value):
        try:
            request = json.loads(value)
            return json.dumps(dict(ok=True, data=self.handle(request["action"], request.get("payload"))), ensure_ascii=False)
        except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
            return json.dumps(dict(ok=False, error=str(exc)), ensure_ascii=False)


_android_service = None


def android_request(root, value):
    global _android_service
    if _android_service is None:
        _android_service = AppService(root, "android")
    return _android_service.request(value)
