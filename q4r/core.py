from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from threading import RLock
from functools import wraps
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from . import __version__
from .names import name_forms, name_match, country_context


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
    country: str = ""
    aliases: list[str] = field(default_factory=list)
    name_variants: list[str] = field(default_factory=list)
    expand_names: bool = True

    def __post_init__(self):
        self.name = " ".join(clean(self.name).split())
        self.username = clean(self.username).removeprefix("@")
        self.email = clean(self.email)
        self.country = " ".join(clean(self.country).split())
        if not isinstance(self.name_variants, (list, tuple)) or len(self.name_variants) > 3 or any(not isinstance(x, str) for x in self.name_variants):
            raise ValueError("Provide at most three additional full-name spellings")
        self.name_variants = list(dict.fromkeys(" ".join(clean(value).split()) for value in self.name_variants))
        if self.name_variants and not self.name:
            raise ValueError("Provide the full name before additional name spellings")
        if any(len(value) > 200 or len(value.split()) < 2 for value in self.name_variants):
            raise ValueError("Additional name spellings need 2+ parts and at most 200 characters")
        if len(self.country) > 80:
            raise ValueError("Country: at most 80 characters")
        if not isinstance(self.aliases, (list, tuple)) or len(self.aliases) > 5 or any(not isinstance(x, str) for x in self.aliases):
            raise ValueError("Provide at most five additional usernames for the same subject")
        self.aliases = list(dict.fromkeys(clean(value).removeprefix("@") for value in self.aliases))
        self.aliases = [value for value in self.aliases if value and value != self.username]
        if self.name and len(self.name.split()) < 2:
            raise ValueError("Name must contain at least two parts; full Arabic names are supported")
        if any(not re.fullmatch(r"[\w.-]{1,100}", value) for value in self.usernames()):
            raise ValueError("Username: 1-100 letters, digits, dots, underscores or hyphens")
        if self.email and (len(self.email) > 254 or not re.fullmatch(r'[^\s@<>"\\]+@[^\s@<>"\\]+\.[^\s@<>"\\]+', self.email)):
            raise ValueError("Invalid email address")
        if not any((self.name, self.username, self.email, self.aliases)):
            raise ValueError("Provide a name, username or email")

    def usernames(self):
        return ([self.username] if self.username else []) + self.aliases

    def name_forms(self):
        return name_forms(self.name, self.name_variants, self.expand_names)

    def label(self):
        return self.name or self.username or (self.aliases[0] if self.aliases else self.email)


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
    if any(normalize(str(attributes.get("username", ""))) == normalize(value) for value in target.usernames()):
        signals.append("exact public username; ownership unverified")
        score += 25
    match = name_match(target, str(attributes.get("name") or ""))
    if match:
        points = {"exact":25, "extended":20, "spelling_candidate":10}
        signals.append(match[0] + " public display-name match; identity unverified")
        score += points[match[0]]
    elif target.name and name_match(target, title + " " + summary):
        signals.append("full name/spelling phrase in returned text; review required")
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
        self.attachments = []
        self.run = {}
        self.assistance = {}

    def for_username(self, username, include_name=True):
        return CaseView(self, replace(self.target, username=username, aliases=[],
                                     name=self.target.name if include_name else "",
                                     name_variants=self.target.name_variants if include_name else []))

    @locked
    def record(self, source, url, status, body, elapsed, error="", evidence_type="http_response"):
        evidence_id = "E" + str(len(self.requests) + 1).zfill(5)
        # Store response bodies only; credentials/authorization headers are never persisted.
        filename = "evidence/" + evidence_id + ".bin"
        (self.directory / filename).write_bytes(body)
        entry = dict(id=evidence_id, source=source, url=url, status=status,
                     observed_at=now(), sha256=hashlib.sha256(body).hexdigest(),
                     file=filename, bytes=len(body), elapsed_ms=round(elapsed * 1000), error=error,
                     evidence_type=evidence_type)
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
                    attachments=list(self.attachments),
                    run=dict(self.run),
                    assistance=dict(self.assistance),
                    name_search=dict(forms=self.target.name_forms(), country_context=country_context(self.target.country)),
                    interpretation="Scores rank textual evidence, not probability or proof of identity. "
                    "All relationships are unverified leads. No match does not prove absence.")


class CaseView:
    """Per-lookup inputs with the parent's synchronized evidence store and scoring."""
    def __init__(self, case, target):
        self.case, self.target = case, target

    def __getattr__(self, name):
        return getattr(self.case, name)


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
    # Names stay intact: never search individual given/father/family-name tokens.
    identities = [("name" if index == 0 else "name_variant", row["value"]) for index, row in enumerate(target.name_forms())]
    identities += [(k, v) for k, v in (("username", target.username), ("email", target.email)) if v]
    identities += [("alias", value) for value in target.aliases]
    queries = [(k, quoted(v)) for k, v in identities]
    if target.name and target.username:
        queries.append(("name+username", quoted(target.name) + " " + quoted(target.username)))
    if target.country:
        scoped = [(k + "+country", quoted(v) + " " + quoted(target.country)) for k, v in identities]
        localized = []
        for term in country_context(target.country)["terms"]:
            if term != target.country:
                localized += [(k+"+country_variant", quoted(v)+" "+quoted(term)) for k, v in identities]
        queries = scoped + localized + queries
    for kind, term in queries:
        yield dict(category="global", input=kind, query=term,
                   url="https://www.google.com/search?" + urlencode({"q": term}), status="manual_search_link")
    for site in sites:
        for kind, value in identities:
            if target.country:
                q = "site:" + site["domain"] + " " + quoted(value) + " " + quoted(target.country)
                yield dict(category=site.get("category", "other"), input=kind+"+country", query=q,
                           url="https://www.google.com/search?" + urlencode({"q": q}), status="manual_search_link")
            q = "site:" + site["domain"] + " " + quoted(value)
            yield dict(category=site.get("category", "other"), input=kind, query=q,
                       url="https://www.google.com/search?" + urlencode({"q": q}), status="manual_search_link")


def prioritize_plan(plan):
    """Distribute a bounded web budget across identifiers and platform domains.

    Preserve original entries (and their evidence status) without letting many
    country/transliteration queries consume the entire first page budget.
    """
    from collections import deque
    preferred = ['facebook.com', 'instagram.com', 'x.com', 'youtube.com', 'tiktok.com',
                 'linkedin.com', 't.me', 'github.com', 'reddit.com', 'pinterest.com']
    weights = {'username': 0, 'alias': 1, 'name': 2, 'email': 3, 'name+username': 4}
    globals_, domains, ai = [], {}, []
    for entry in plan:
        if entry.get('input') == 'ai_suggested':
            ai.append(entry)
        elif entry.get('category') == 'global':
            globals_.append(entry)
        else:
            match = re.search(r'(?:^|\s)site:([^\s]+)', entry.get('query', ''))
            if match:domains.setdefault(match.group(1), []).append(entry)
            else:globals_.append(entry)
    def rank(row):
        kind = row.get('input', '')
        return (0 if kind in weights else 1, weights.get(kind, 20), '+' in kind)
    globals_.sort(key=rank)
    seed = [x for x in globals_ if x.get('input') in ('username', 'alias', 'name', 'email')][:4]
    output, seen = [], set()
    def add(entry):
        if entry['query'] not in seen:
            seen.add(entry['query']);output.append(entry)
    for entry in seed:add(entry)
    remainder = deque(x for x in globals_ if x['query'] not in seen)
    ai = deque(ai)
    order = sorted(domains, key=lambda d: (preferred.index(d) if d in preferred else len(preferred)))
    buckets = deque(deque(sorted(domains[d], key=rank)) for d in order)
    count = 0
    while buckets:
        bucket = buckets.popleft();add(bucket.popleft());count += 1
        if bucket:buckets.append(bucket)
        if count % 4 == 0:
            if remainder:add(remainder.popleft())
            if ai:add(ai.popleft())
    for entry in [*remainder, *ai]:add(entry)
    return output
