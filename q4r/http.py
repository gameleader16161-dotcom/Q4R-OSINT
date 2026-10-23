"""Bounded JSON transport with per-host pacing and auditable failures."""
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

HOSTS = {"api.github.com", "gitlab.com", "keybase.io", "archive.org", "openlibrary.org", "api.search.brave.com"}
MAX_BYTES = 4 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SourceError(Exception):
    pass


class Client:
    def __init__(self, case, timeout=15, interval=1.1):
        self.case, self.timeout, self.interval = case, timeout, interval
        self.last = {}
        self.opener = build_opener(NoRedirect())

    def get(self, source, url, headers=None):
        p = urlsplit(url)
        if p.scheme != "https" or p.hostname not in HOSTS or p.port not in (None, 443) or p.username or p.password:
            raise SourceError("Endpoint is not on the built-in HTTPS allowlist")
        for attempt in range(2):
            time.sleep(max(0, self.interval - (time.monotonic() - self.last.get(p.hostname, 0))))
            start, status, body, error = time.monotonic(), 0, b"", ""
            retry_after = 1
            try:
                request = Request(url, headers={"User-Agent": "Q4R/0.1 (+https://github.com/gameleader16161-dotcom/Q4R-OSINT)",
                                               "Accept": "application/json", **(headers or {})})
                with self.opener.open(request, timeout=self.timeout) as response:
                    status = response.status
                    body = response.read(MAX_BYTES + 1)
            except HTTPError as exc:
                status, body = exc.code, exc.read(MAX_BYTES + 1)
                error = "HTTP " + str(status)
                value = exc.headers.get("Retry-After", "1")
                retry_after = int(value) if value.isdigit() else 1
            except (URLError, TimeoutError, OSError) as exc:
                # No exception text: proxy URLs or credentials could be embedded in it.
                error = type(exc).__name__
            finally:
                self.last[p.hostname] = time.monotonic()
            if len(body) > MAX_BYTES:
                body, error = body[:MAX_BYTES], "Response exceeded 4 MiB; truncated"
            eid = self.case.record(source, url, status, body, time.monotonic() - start, error)
            if status in (429, 502, 503, 504) and attempt == 0 and retry_after <= 5:
                time.sleep(max(1, retry_after))
                continue
            if error:
                raise SourceError(f"{error}; evidence {eid}")
            try:
                return json.loads(body), eid
            except (ValueError, UnicodeError):
                raise SourceError(f"Invalid JSON; evidence {eid}") from None
