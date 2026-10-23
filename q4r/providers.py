import os
import re
from urllib.parse import quote, urlencode, urlsplit

from .core import quoted, safe_url
from .http import SourceError


def endpoint(base, **params):
    return base + "?" + urlencode(params, doseq=True)


def github(case, client, limit):
    t = case.target
    if not t.username:
        return "skipped", "username required"
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", t.username):
        return "skipped", "GitHub handles allow ASCII letters/digits/hyphens only (1-39 characters, no edge hyphen); " \
                          "this handle is retained unchanged for other sources and web searches"
    headers = {}
    if os.getenv("Q4R_GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["Q4R_GITHUB_TOKEN"]
    url = "https://api.github.com/users/" + quote(t.username, safe="")
    d, e = client.get("github", url, headers)
    if not d.get("login"):
        return "error", "unexpected user response"
    case.add("github", d.get("name") or d["login"], d["html_url"], d.get("bio") or "", e, t.username,
             {"username": d["login"], "name": d.get("name"), "email": d.get("email"),
              "public_repos": d.get("public_repos"), "created_at": d.get("created_at"),
              "website": safe_url(d.get("blog", ""))})
    return "ok", "public profile retrieved; ownership not verified"


def gitlab(case, client, limit):
    if not case.target.username:
        return "skipped", "username required"
    d, e = client.get("gitlab", endpoint("https://gitlab.com/api/v4/users", username=case.target.username))
    for item in d[:limit]:
        case.add("gitlab", item.get("name", ""), item.get("web_url", ""), item.get("bio") or "", e,
                 case.target.username, {"username": item.get("username"), "name": item.get("name")})
    return "ok", f"{len(d)} profiles returned"


def keybase(case, client, limit):
    t = case.target
    if not t.username or not re.fullmatch(r"[A-Za-z0-9_]{2,16}", t.username):
        return "skipped", "Keybase username requires 2-16 ASCII letters/digits/underscores"
    d, e = client.get("keybase", endpoint("https://keybase.io/_/api/1.0/user/lookup.json", usernames=t.username))
    if d.get("status", {}).get("code") != 0:
        raise SourceError("Keybase API did not return OK")
    for item in (d.get("them") or [])[:limit]:
        if not item:
            continue
        username = item["basics"]["username"]
        profile = item.get("profile", {}) or {}
        case.add("keybase", profile.get("full_name") or username,
                 "https://keybase.io/" + quote(username, safe=""), profile.get("bio") or "", e, t.username,
                 {"username": username, "name": profile.get("full_name")})
    return "ok", "public lookup completed"


def archive(case, client, limit):
    if not case.target.name:
        return "skipped", "name required for archive metadata search"
    q = quoted(case.target.name)
    if len(q) > 600:
        return "skipped", "name exceeds archive query budget; use a shorter supplied form"
    d, e = client.get("archive", endpoint("https://archive.org/advancedsearch.php", q=q, output="json",
        rows=limit, page=1, **{"fl[]": ["identifier", "title", "description", "creator"]}))
    for item in d.get("response", {}).get("docs", []):
        case.add("archive", item.get("title", "Archive item"),
                 "https://archive.org/details/" + quote(item["identifier"], safe=""),
                 str(item.get("description", "")), e, q, {"creator": item.get("creator")}, kind="document")
    return "ok", "archive metadata search completed (not a full-text web archive search)"


def openlibrary(case, client, limit):
    if not case.target.name:
        return "skipped", "name required"
    q = quoted(case.target.name)
    if len(q) > 600:
        return "skipped", "name exceeds query budget"
    d, e = client.get("openlibrary", endpoint("https://openlibrary.org/search.json", q=q, limit=limit,
                                             fields="key,title,author_name,first_publish_year"))
    for item in d.get("docs", []):
        case.add("openlibrary", item.get("title", "Book"), "https://openlibrary.org" + item["key"],
                 ", ".join(item.get("author_name", [])), e, q,
                 {"authors": item.get("author_name", []), "first_publish_year": item.get("first_publish_year")}, kind="document")
    return "ok", "catalog search completed"


def brave(case, client, limit, budget):
    token = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    if not token:
        return "skipped", "set BRAVE_SEARCH_API_KEY for automatic web/social search; manual links exported"
    count = 0
    for entry in case.plan[:budget]:
        q = entry["query"]
        if len(q) > 600 or len(q.split()) > 75:
            entry["status"] = "skipped_query_limit"
            continue
        try:
            d, e = client.get("brave", endpoint("https://api.search.brave.com/res/v1/web/search", q=q,
                             count=limit, text_decorations="false"), {"X-Subscription-Token": token})
        except SourceError:
            entry["status"] = "error"
            raise
        for item in d.get("web", {}).get("results", []):
            case.add("brave", item.get("title", "Web result"), item.get("url", ""),
                     item.get("description", ""), e, q, kind="web_search_lead")
        entry["status"] = "searched"
        count += 1
    return "ok", f"{count} web queries completed; budget {budget}; search index coverage only"


def wayback(case, client, limit):
    urls = list(dict.fromkeys(f.url for f in case.findings if f.source != "wayback"))[:limit]
    if not urls:
        return "skipped", "no discovered URLs to look up"
    for url in urls:
        d, e = client.get("wayback", endpoint("https://archive.org/wayback/available", url=url))
        closest = d.get("archived_snapshots", {}).get("closest", {})
        if closest.get("available"):
            case.add("wayback", "Archived snapshot", closest.get("url", ""),
                     "Snapshot of " + url, e, url,
                     {"original_url": url, "timestamp": closest.get("timestamp")}, kind="archive_snapshot")
    return "ok", f"looked up {len(urls)} discovered URLs; snapshots may contain outdated data"


PROVIDERS = {"github": github, "gitlab": gitlab, "keybase": keybase,
             "archive": archive, "openlibrary": openlibrary}
