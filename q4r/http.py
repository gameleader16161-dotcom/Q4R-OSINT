"""Bounded JSON transport with per-host pacing and auditable failures."""
import json
from dataclasses import dataclass
import time
from threading import Event, Lock
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import __version__

HOSTS = {"api.github.com", "gitlab.com", "keybase.io", "archive.org", "openlibrary.org", "api.search.brave.com",
         "public.api.bsky.app", "mastodon.social", "www.reddit.com", "dev.to", "hacker-news.firebaseio.com",
         "t.me", "www.instagram.com", "www.tiktok.com", "www.youtube.com", "www.facebook.com", "x.com",
         "www.pinterest.com"}
MAX_BYTES = 4 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SourceError(Exception):
    def __init__(self, message, status=0, evidence_id=""):
        super().__init__(message)
        self.status, self.evidence_id = status, evidence_id


@dataclass
class Response:
    status: int
    body: bytes
    evidence_id: str
    url: str
    location: str = ""
    content_type: str = ""

    @property
    def text(self):
        return self.body.decode("utf-8", errors="replace")


class Client:
    def __init__(self, case, timeout=15, interval=1.1):
        self.case, self.timeout, self.interval = case, timeout, interval
        self.last = {}
        self.opener = build_opener(NoRedirect())
        self.cancelled = Event()
        self.host_locks = {host: Lock() for host in HOSTS}

    def get(self, source, url, headers=None):
        response = self.fetch(source, url, headers)
        if response.status != 200:
            raise SourceError(f"HTTP {response.status}; evidence {response.evidence_id}", response.status, response.evidence_id)
        try:
            return json.loads(response.body), response.evidence_id
        except (ValueError, UnicodeError):
            raise SourceError(f"Invalid JSON; evidence {response.evidence_id}", response.status, response.evidence_id) from None

    def fetch(self, source, url, headers=None):
        p = urlsplit(url)
        if p.scheme != "https" or p.hostname not in HOSTS or p.port not in (None, 443) or p.username or p.password:
            raise SourceError("Endpoint is not on the built-in HTTPS allowlist")
        with self.host_locks[p.hostname]:
            return self._fetch(source, url, p.hostname, headers)

    def _fetch(self, source, url, host, headers):
        for attempt in range(2):
            delay = max(0, self.interval - (time.monotonic() - self.last.get(host, 0)))
            if self.cancelled.wait(delay):
                raise SourceError("Collection cancelled")
            start, status, body, error = time.monotonic(), 0, b"", ""
            location, content_type = "", ""
            retry_after = 1
            try:
                request = Request(url, headers={"User-Agent": f"Q4R/{__version__} (+https://github.com/gameleader16161-dotcom/Q4R-OSINT)",
                                               "Accept": "application/json", **(headers or {})})
                with self.opener.open(request, timeout=self.timeout) as response:
                    status = response.status
                    body = response.read(MAX_BYTES + 1)
                    response_headers = getattr(response, "headers", {})
                    location = response_headers.get("Location", "")
                    content_type = response_headers.get("Content-Type", "")
            except HTTPError as exc:
                status, body = exc.code, exc.read(MAX_BYTES + 1)
                error = "HTTP " + str(status)
                location = exc.headers.get("Location", "")
                content_type = exc.headers.get("Content-Type", "")
                value = exc.headers.get("Retry-After", "1")
                retry_after = int(value) if value.isdigit() else 1
            except (URLError, TimeoutError, OSError) as exc:
                # No exception text: proxy URLs or credentials could be embedded in it.
                error = type(exc).__name__
            finally:
                self.last[host] = time.monotonic()
            if len(body) > MAX_BYTES:
                body, error = body[:MAX_BYTES], "Response exceeded 4 MiB; truncated"
            eid = self.case.record(source, url, status, body, time.monotonic() - start, error)
            if status in (429, 502, 503, 504) and attempt == 0 and retry_after <= 5:
                if self.cancelled.wait(max(1, retry_after)):
                    raise SourceError("Collection cancelled")
                continue
            if not status or "truncated" in error:
                raise SourceError(f"{error}; evidence {eid}", status, eid)
            return Response(status, body, eid, url, location, content_type)
