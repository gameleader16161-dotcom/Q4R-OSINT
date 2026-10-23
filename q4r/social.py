"""Public social profile checks. HTTP 200 alone is never an existence signal."""
import html
from html.parser import HTMLParser
import json
import os
import re
from functools import partial
from urllib.parse import quote, urlencode, urlsplit

from .core import normalize, public_text, safe_url


def url(base, **params):
    return base + "?" + urlencode(params)


def text_only(value):
    return public_text(re.sub(r"<[^>]*>", " ", html.unescape(str(value))))


def status_for(case, source, start):
    checks = [c for c in case.checks[start:] if c["source"] == source]
    states = {c["status"] for c in checks}
    count = sum(f.source == source for f in case.findings)
    status = "ok" if "found" in states else "not_found" if states == {"not_found"} else "skipped"
    for state in ("unconfirmed", "blocked", "error"):
        if state in states:
            status = "partial" if "found" in states else state
    return status, f"{count} retrieved profiles/leads; " + ", ".join(f'{s}: {sum(c["status"] == s for c in checks)}' for s in sorted(states))


def request_json(case, client, source, endpoint, query, headers=None):
    response = client.fetch(source, endpoint, headers)
    status = "ok"
    if response.status in (401, 403, 429):
        status = "blocked"
    elif response.status == 404:
        status = "not_found"
    elif response.status != 200:
        status = "error"
    try:
        data = json.loads(response.body)
    except (ValueError, UnicodeError):
        data = None
        if status == "ok":
            status = "unconfirmed"
    if response.status == 400 and isinstance(data, dict) and data.get("error") in ("ProfileNotFound", "ActorNotFound"):
        status = "not_found"
    if status != "ok":
        case.check(source, status, f"HTTP {response.status}; " + ("no profile evidence" if status == "unconfirmed" else status),
                   endpoint, response.evidence_id, query)
    return data, response.evidence_id, status


def add_profile(case, source, data, profile_url, query, eid, username, name, summary, **extra):
    if not safe_url(profile_url):
        raise ValueError("Profile URL missing or invalid")
    case.add(source, name or username, profile_url, text_only(summary), eid, query,
             {"username": username, "name": name, "existence": "public profile data returned; ownership unverified", **extra}, kind="public_profile")
    case.check(source, "found", "Public profile data returned; identity/ownership not established", profile_url, eid, query)


def bluesky(case, client, limit):
    source, start, t = "bluesky", len(case.checks), case.target
    if not t.username and not t.name:
        return "skipped", "name or username required"
    if t.username and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", t.username) and "_" not in t.username:
        actor = t.username if "." in t.username else t.username + ".bsky.social"
        d, eid, status = request_json(case, client, source,
            url("https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile", actor=actor), actor)
        if status == "ok":
            if not isinstance(d, dict) or not d.get("did") or not d.get("handle"):
                raise ValueError("Bluesky profile schema changed")
            add_profile(case, source, d, "https://bsky.app/profile/" + quote(d["handle"], safe=""), actor, eid,
                        d["handle"], d.get("displayName", ""), d.get("description", ""), did=d["did"])
    # Search is independent of the platform's exact handle grammar; returned accounts are candidates.
    queries = list(dict.fromkeys(q for q in (t.username, t.name) if q and len(q) <= 300))
    for query in queries:
        d, eid, status = request_json(case, client, source,
            url("https://public.api.bsky.app/xrpc/app.bsky.actor.searchActors", q=query, limit=limit), query)
        if status != "ok":
            continue
        actors = d.get("actors") if isinstance(d, dict) else None
        if not isinstance(actors, list):
            raise ValueError("Bluesky search schema changed")
        if not actors:
            case.check(source, "not_found", "No indexed actor search results; not proof of absence", query=query, evidence_id=eid)
        for item in actors[:limit]:
            if not item.get("did") or not item.get("handle"):
                raise ValueError("Bluesky search profile missing identity fields")
            add_profile(case, source, item, "https://bsky.app/profile/" + quote(item["handle"], safe=""), query, eid,
                        item["handle"], item.get("displayName", ""), item.get("description", ""), did=item["did"],
                        match_type="search candidate; not a verified match to the supplied person")
    return status_for(case, source, start)


def mastodon(case, client, limit):
    source, start, t = "mastodon", len(case.checks), case.target
    token = os.getenv("Q4R_MASTODON_TOKEN", "").strip()
    headers = {"Authorization": "Bearer " + token} if token else None
    if t.username and re.fullmatch(r"[A-Za-z0-9_]{1,100}", t.username):
        d, eid, status = request_json(case, client, source,
            url("https://mastodon.social/api/v1/accounts/lookup", acct=t.username), t.username, headers)
        if status == "ok":
            if not isinstance(d, dict) or not d.get("id") or not d.get("acct"):
                raise ValueError("Mastodon profile schema changed")
            add_profile(case, source, d, d.get("url", ""), t.username, eid, d["acct"], d.get("display_name", ""),
                        d.get("note", ""), instance="mastodon.social", public_id=d["id"])
    elif not token:
        return "skipped", "mastodon.social exact lookup needs an ASCII username; name search needs Q4R_MASTODON_TOKEN"
    if t.name and token:
        d, eid, status = request_json(case, client, source,
            url("https://mastodon.social/api/v1/accounts/search", q=t.name[:300], limit=limit, resolve="false"), t.name, headers)
        if status == "ok":
            if not isinstance(d, list):
                raise ValueError("Mastodon search schema changed")
            for item in d[:limit]:
                add_profile(case, source, item, item.get("url", ""), t.name, eid, item["acct"],
                            item.get("display_name", ""), item.get("note", ""), match_type="display-name search candidate")
            if not d:
                case.check(source, "not_found", "No account search results", query=t.name, evidence_id=eid)
    return status_for(case, source, start)


def simple_api(case, client, limit, source):
    t, start = case.target, len(case.checks)
    patterns = {"reddit": r"[A-Za-z0-9_-]{3,20}", "devto": r"[A-Za-z0-9_]{1,100}", "hackernews": r"[A-Za-z0-9_-]{1,100}"}
    if not t.username or not re.fullmatch(patterns[source], t.username):
        return "skipped", "username is missing or incompatible with this platform"
    handle = quote(t.username, safe="")
    endpoints = {"reddit": f"https://www.reddit.com/user/{handle}/about.json",
                 "devto": url("https://dev.to/api/users/by_username", url=t.username),
                 "hackernews": f"https://hacker-news.firebaseio.com/v0/user/{handle}.json"}
    d, eid, status = request_json(case, client, source, endpoints[source], t.username)
    if status != "ok":
        return status_for(case, source, start)
    if source == "hackernews" and d is None:
        case.check(source, "not_found", "API returned null for this username", endpoints[source], eid, t.username)
    else:
        if source == "reddit":
            d = d.get("data") if isinstance(d, dict) and d.get("kind") == "t2" else None
            if not isinstance(d, dict) or not d.get("id") or normalize(d.get("name", "")) != normalize(t.username):
                raise ValueError("Reddit user object missing or mismatched")
            add_profile(case, source, d, f"https://www.reddit.com/user/{handle}/", t.username, eid,
                        d["name"], "", d.get("subreddit", {}).get("public_description", ""), public_id=d["id"])
        elif source == "devto":
            if not isinstance(d, dict) or not d.get("id") or normalize(d.get("username", "")) != normalize(t.username):
                raise ValueError("DEV username missing or mismatched")
            links = []
            website = safe_url(d.get("website_url", ""))
            if website:
                links.append({"url": website, "relation": "website explicitly published by this profile"})
            for field, base in (("github_username", "https://github.com/"), ("twitter_username", "https://x.com/")):
                value = d.get(field)
                if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", value):
                    links.append({"url": base + value, "relation": "account link explicitly published by this profile; unverified ownership"})
            add_profile(case, source, d, f"https://dev.to/{handle}", t.username, eid, d["username"],
                        d.get("name", ""), d.get("summary", ""), website=website, links=links)
        else:
            if not isinstance(d, dict) or d.get("id") != t.username:
                raise ValueError("HN username missing or mismatched")
            add_profile(case, source, d, url("https://news.ycombinator.com/user", id=t.username), t.username, eid,
                        d["id"], "", d.get("about", ""), created=d.get("created"))
    return status_for(case, source, start)


class ProfilePage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.canonical, self.scripts, self.classes, self.title = {}, [], [], set(), ""
        self.in_script, self.in_title, self.buffer = False, False, []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        self.classes.update((d.get("class") or "").split())
        if tag == "meta":
            self.meta[(d.get("property") or d.get("name") or "").lower()] = d.get("content") or ""
        elif tag == "link" and (d.get("rel") or "").lower() == "canonical":
            self.canonical.append(d.get("href") or "")
        elif tag == "script":
            self.in_script, self.buffer = True, []
        elif tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == "script" and self.in_script:
            self.scripts.append("".join(self.buffer))
            self.in_script, self.buffer = False, []
        elif tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_script:
            self.buffer.append(data)
        if self.in_title:
            self.title += data


def objects(page):
    decoder, count = json.JSONDecoder(), 0
    for script in page.scripts:
        value = script.lstrip()
        initial = re.search(r"(?:var\s+)?ytInitialData\s*=\s*", script)
        if initial:
            value = script[initial.end():]
        if not value.startswith(("{", "[")):
            continue
        try:
            tree, _ = decoder.raw_decode(value)
        except ValueError:
            continue
        stack = [tree]
        while stack and count < 60000:
            node = stack.pop()
            count += 1
            if isinstance(node, dict):
                yield node
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
            elif isinstance(node, list):
                stack.extend(reversed(node))


HTML_SITES = {
    "telegram": ("https://t.me/{handle}", r"[A-Za-z][A-Za-z0-9_]{3,31}"),
    "instagram": ("https://www.instagram.com/{handle}/", r"[A-Za-z0-9_.]{1,30}"),
    "tiktok": ("https://www.tiktok.com/@{handle}", r"[A-Za-z0-9_.]{2,24}"),
    "youtube": ("https://www.youtube.com/@{handle}", r"[\w.-]{3,30}"),
    "facebook": ("https://www.facebook.com/{handle}", r"[A-Za-z0-9.]{5,60}"),
    "x": ("https://x.com/{handle}", r"[A-Za-z0-9_]{1,15}"),
    "pinterest": ("https://www.pinterest.com/{handle}/", r"[A-Za-z0-9_]{3,30}"),
}


def same_profile_url(left, right):
    try:
        a, b = urlsplit(left), urlsplit(right)
        return a.scheme in ("http", "https") and (a.hostname or "").removeprefix("www.").lower() == \
            (b.hostname or "").removeprefix("www.").lower() and a.path.rstrip("/").casefold() == b.path.rstrip("/").casefold()
    except ValueError:
        return False


def classify_html(source, handle, response):
    """Return (state, details, profile fields). Unknown pages never become findings."""
    if response.status in (401, 403, 429):
        return "blocked", f"HTTP {response.status}: platform denied or limited the request", {}
    if 300 <= response.status < 400:
        return "blocked", "Platform redirected the request; login/consent/redirect was not followed", {}
    if response.status == 404:
        return "not_found", "Platform returned HTTP 404 for this profile URL", {}
    if response.status != 200:
        return "error", f"HTTP {response.status}", {}
    page = ProfilePage()
    page.feed(response.text)
    title = page.title.casefold().strip()
    if any(marker in title for marker in ("just a moment", "access denied", "verify you are human", "security check", "log in", "login", "sign in", "captcha")):
        return "blocked", "Login, challenge or access-denied page returned", {}
    fields = {"name": page.meta.get("og:title", ""), "summary": page.meta.get("og:description", page.meta.get("description", "")),
              "username": handle, "profile_url": response.url, "method": ""}
    if source == "telegram":
        if {"tgme_page_title", "tgme_page_extra"} <= page.classes and fields["name"] and not fields["name"].startswith("Telegram: Contact @"):
            fields["method"] = "Telegram public profile/channel title and account detail block"
            return "found", fields["method"], fields
        # Telegram's generic contact page is also returned for non-existent handles.
        return "unconfirmed", "Generic Telegram contact page; no public profile/channel evidence", {}
    canonical = any(same_profile_url(item, response.url) for item in page.canonical + [page.meta.get("og:url", "")])
    claimed = page.meta.get("profile:username", "").lstrip("@")
    title_handle = re.search(r"\(@?" + re.escape(handle) + r"\)", fields["name"], re.I)
    if canonical and page.meta.get("og:type", "").lower() == "profile" and (normalize(claimed) == normalize(handle) or title_handle):
        fields["method"] = "Matching canonical profile URL and explicit username in profile metadata"
        return "found", fields["method"], fields
    for node in objects(page):
        if source == "youtube" and isinstance(node.get("channelMetadataRenderer"), dict):
            channel = node["channelMetadataRenderer"]
            urls = channel.get("ownerUrls", []) + [channel.get("vanityChannelUrl", "")]
            if channel.get("externalId", "").startswith("UC") and any(same_profile_url(u, response.url) for u in urls):
                fields.update(name=channel.get("title", ""), summary=channel.get("description", ""),
                              profile_url="https://www.youtube.com/channel/" + quote(channel["externalId"], safe=""),
                              method="YouTube channel ID and matching owner/vanity handle URL")
                return "found", fields["method"], fields
        if not canonical:
            continue
        if source == "facebook" and normalize(str(node.get("userVanity", ""))) == normalize(handle) and str(node.get("userID", "0")).isdigit() and int(node.get("userID", "0")) > 0:
            fields["method"] = "Facebook profile ID and matching vanity username with canonical URL"
            return "found", fields["method"], fields
        if source in ("instagram", "tiktok", "pinterest"):
            username = node.get("uniqueId") if source == "tiktok" else node.get("username")
            if isinstance(username, str) and normalize(username) == normalize(handle) and (node.get("id") or node.get("pk") or node.get("secUid")):
                fields.update(name=node.get("nickname") or node.get("full_name") or fields["name"], method="Matching structured public profile ID/username and canonical URL")
                return "found", fields["method"], fields
    return "unconfirmed", "HTTP 200 without sufficient matching profile evidence (may require JavaScript/login or have changed layout)", {}


def html_profile(case, client, limit, source):
    t = case.target
    template, pattern = HTML_SITES[source]
    if not t.username or not re.fullmatch(pattern, t.username):
        return "skipped", "username missing or incompatible with this platform; supplied spelling was not changed"
    endpoint = template.format(handle=quote(t.username, safe=""))
    response = client.fetch(source, endpoint, {"Accept": "text/html,application/xhtml+xml"})
    state, detail, fields = classify_html(source, t.username, response)
    case.check(source, state, detail, endpoint, response.evidence_id, t.username)
    if state == "found":
        case.add(source, fields["name"] or t.username, fields["profile_url"], text_only(fields["summary"]),
                 response.evidence_id, t.username, {"username": t.username, "existence": "public profile evidence returned; ownership unverified",
                 "detection_method": fields["method"]}, kind="public_profile")
        return "ok", "public profile evidence retrieved; ownership not verified"
    return state, detail


SOCIAL_PROVIDERS = {"bluesky": bluesky, "mastodon": mastodon,
                    **{source: partial(simple_api, source=source) for source in ("reddit", "devto", "hackernews")},
                    **{source: partial(html_profile, source=source) for source in HTML_SITES}}
