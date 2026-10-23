from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from threading import RLock
from functools import wraps
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from . import __version__


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def locked(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return wrapped


def public_text(value):
    # Source text must remain valid XML and must not inject terminal control codes.
    return "".join(c for c in str(value) if c in "\n\t" or
                   (ord(c) >= 32 and not unicodedata.category(c).startswith("C")
                    and ord(c) not in (0xfffe, 0xffff)))


def clean(value):
    value = unicodedata.normalize("NFKC", value.strip())
    if any(unicodedata.category(c).startswith("C") for c in value):
        raise ValueError("Control/format characters are not allowed in input")
    return value


@dataclass
class Target:
    name: str = ""
    username: str = ""
    email: str = ""

    def __post_init__(self):
        self.name = " ".join(clean(self.name).split())
        self.username = clean(self.username).removeprefix("@")
        self.email = clean(self.email)
        if self.name and len(self.name.split()) < 2:
            raise ValueError("Name must contain at least two parts; full Arabic names are supported")
        if self.username and not re.fullmatch(r"[\w.-]{1,100}", self.username):
            raise ValueError("Username: 1-100 letters, digits, dots, underscores or hyphens")
        if self.email and (len(self.email) > 254 or not re.fullmatch(r'[^\s@<>"\\]+@[^\s@<>"\\]+\.[^\s@<>"\\]+', self.email)):
            raise ValueError("Invalid email address")
        if not any((self.name, self.username, self.email)):
            raise ValueError("Provide a name, username or email")


def safe_url(url):
    """Allow only ordinary public HTTP(S) links in reports, never executable URLs."""
    try:
        p = urlsplit(str(url))
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
            return ""
        if any(ord(c) < 32 for c in str(url)):
            return ""
        return urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, ""))
    except ValueError:
        return ""


@dataclass
class Finding:
    id: str
    source: str
    title: str
    url: str
    summary: str
    observed_at: str
    evidence_id: str
    query: str
    score: int
    signals: list[str]
    kind: str = "candidate"
    attributes: dict = field(default_factory=dict)


def score_match(target, title, summary, attributes):
    signals, score = [], 0
    # Independent fields only. Do not score URLs or search-query echoes.
    if target.username and normalize(str(attributes.get("username", ""))) == normalize(target.username):
        signals.append("exact public username; ownership unverified")
        score += 25
    if target.name and normalize(str(attributes.get("name", ""))) == normalize(target.name):
        signals.append("exact public display name; identity unverified")
        score += 25
    elif target.name and normalize(target.name) in normalize(title + " " + summary):
        signals.append("name phrase in returned text")
        score += 10
    if target.email and normalize(str(attributes.get("email", ""))) == normalize(target.email):
        signals.append("exact email explicitly published by source")
        score += 40
    if not signals:
        signals.append("source returned a search lead; review required")
    return min(score, 90), signals


class Case:
    def __init__(self, target, directory):
        self.lock = RLock()
        self.target = target
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.evidence_dir = self.directory / "evidence"
        self.evidence_dir.mkdir(mode=0o700)
        self.started_at = now()
        self.findings = []
        self.requests = []
        self.coverage = []
        self.plan = []
        self.seen = set()
        self.checks = []

    @locked
    def record(self, source, url, status, body, elapsed, error=""):
        evidence_id = "E" + str(len(self.requests) + 1).zfill(5)
        # Store response bodies only; credentials/authorization headers are never persisted.
        filename = "evidence/" + evidence_id + ".bin"
        (self.directory / filename).write_bytes(body)
        entry = dict(id=evidence_id, source=source, url=url, status=status,
                     observed_at=now(), sha256=hashlib.sha256(body).hexdigest(),
                     file=filename, bytes=len(body), elapsed_ms=round(elapsed * 1000), error=error)
        self.requests.append(entry)
        with (self.directory / "requests.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return evidence_id

    @locked
    def add(self, source, title, url, summary, evidence_id, query, attributes=None, kind="candidate"):
        url = safe_url(url)
        if not url or (source, url, query) in self.seen:
            return
        self.seen.add((source, url, query))
        attributes = attributes or {}
        title, summary = public_text(title)[:1000], public_text(summary)[:3000]
        score, signals = score_match(self.target, title, summary, attributes)
        self.findings.append(Finding("F" + str(len(self.findings) + 1).zfill(5), source,
            title, url, summary, now(), evidence_id, query,
            score, signals, kind, attributes))

    @locked
    def check(self, source, status, detail, url="", evidence_id="", query=""):
        self.checks.append(dict(source=source, status=status, detail=detail, url=safe_url(url),
                                evidence_id=evidence_id, query=query, observed_at=now()))

    @locked
    def data(self):
        return dict(schema_version=1, tool="Q4R", version=__version__, started_at=self.started_at,
                    target=asdict(self.target), findings=[asdict(x) for x in self.findings],
                    requests=list(self.requests), coverage=list(self.coverage), plan=list(self.plan),
                    checks=list(self.checks),
                    interpretation="Scores rank textual evidence, not probability or proof of identity. "
                    "All relationships are unverified leads. No match does not prove absence.")


def load_sites(path=None):
    path = Path(path) if path else Path(__file__).with_name("sites.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or len(data) > 500:
        raise ValueError("Sites registry must be a list of at most 500 entries")
    for item in data:
        if not isinstance(item, dict) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}", item.get("domain", "")):
            raise ValueError("Invalid registry domain")
    return data


def quoted(value):
    # Neutralize operators embedded in a supplied identity before building queries.
    return '"' + value.replace('"', " ").replace("\\", " ") + '"'


def build_plan(target, sites):
    identities = [(k, v) for k, v in asdict(target).items() if v]
    queries = [(k, quoted(v)) for k, v in identities]
    if target.name and target.username:
        queries.append(("name+username", quoted(target.name) + " " + quoted(target.username)))
    for kind, term in queries:
        yield dict(category="global", input=kind, query=term,
                   url="https://www.google.com/search?" + urlencode({"q": term}), status="manual_search_link")
    for site in sites:
        for kind, value in identities:
            q = "site:" + site["domain"] + " " + quoted(value)
            yield dict(category=site.get("category", "other"), input=kind, query=q,
                       url="https://www.google.com/search?" + urlencode({"q": q}), status="manual_search_link")
