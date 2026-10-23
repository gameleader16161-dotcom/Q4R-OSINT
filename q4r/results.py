"""Readable findings and account summaries for both old and new cases."""
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from .core import public_text, safe_url


PLATFORMS = {
    "instagram": "Instagram", "facebook": "Facebook", "tiktok": "TikTok", "youtube": "YouTube",
    "x": "X", "telegram": "Telegram", "bluesky": "Bluesky", "mastodon": "Mastodon",
    "pinterest": "Pinterest", "reddit": "Reddit", "github": "GitHub", "gitlab": "GitLab",
    "keybase": "Keybase", "devto": "DEV", "hackernews": "Hacker News",
}


def account_from_url(value):
    value = safe_url(value)
    if not value:
        return None
    p = urlsplit(value)
    host, parts = (p.hostname or "").removeprefix("www.").lower(), p.path.strip("/").split("/")
    first = parts[0] if parts else ""
    if host == "bsky.app" and len(parts) == 2 and first == "profile":
        return "bluesky", parts[1]
    if host == "reddit.com" and len(parts) >= 2 and first in ("u", "user"):
        return "reddit", parts[1]
    if host == "youtube.com" and first.startswith("@"):
        return "youtube", first[1:]
    if host == "mastodon.social" and first.startswith("@"):
        return "mastodon", first[1:]
    hosts = {"instagram.com":"instagram", "facebook.com":"facebook", "tiktok.com":"tiktok", "x.com":"x",
             "twitter.com":"x", "t.me":"telegram", "pinterest.com":"pinterest", "github.com":"github",
             "gitlab.com":"gitlab", "keybase.io":"keybase", "dev.to":"devto"}
    reserved = {"p", "reel", "reels", "stories", "explore", "accounts", "search", "login", "signup", "share", "watch", "intent", "settings", "i", "home", "notifications", "profile.php"}
    if host in hosts and len(parts) == 1 and first and first.casefold() not in reserved:
        return hosts[host], first.lstrip("@")
    return None


def email_summary_line(data):
    """Summarize explicit public email fields, never infer account registration."""
    email = data.get("target", {}).get("email", "")
    if not email:
        return ""

    def email_key(value):
        if not isinstance(value, str) or value.count("@") != 1:
            return None
        local, domain = value.strip().rsplit("@", 1)
        if not local or not domain:
            return None
        # Do not collapse Gmail dots, plus aliases or local-part case.
        return local, domain.casefold()

    def cell(value):
        return " ".join(public_text(value).split()).replace("|", "\\|")

    evidence = {r.get("id"): r for r in data.get("requests", []) if isinstance(r, dict)}
    linked = {}
    for finding in data.get("findings", []):
        source = finding.get("source", "")
        attrs = finding.get("attributes", {})
        if source not in PLATFORMS or not isinstance(attrs, dict):
            continue
        direct = finding.get("kind") == "public_profile" or source in ("github", "gitlab", "keybase")
        record = evidence.get(finding.get("evidence_id"), {})
        if (not direct or "candidate" in str(attrs.get("match_type", "")).lower()
                or not attrs.get("username") or not safe_url(finding.get("url", ""))
                or email_key(email) is None or email_key(attrs.get("email")) != email_key(email)
                or not record or record.get("source") != source or record.get("error")
                or record.get("status") != 200
                or record.get("evidence_type", "http_response") != "http_response"):
            continue
        account = f'{cell(attrs["username"])} [public email: {cell(record["id"])}]'
        if account not in linked.setdefault(source, []):
            linked[source].append(account)
    fields = ["Email: " + cell(email)]
    for source, label in PLATFORMS.items():
        fields.append(label + ": " + (", ".join(linked[source]) if source in linked else "غير مثبت / unverified"))
    return " | ".join(fields)


def account_lines(data):
    lines = ["Q4R — account summary / ملخص الحسابات", "",
             "Retrieved profiles and search candidates are NOT proof of a person's identity.",
             "X = no result returned by the checked source; ? = blocked, skipped or unconfirmed.",
             "X لا يثبت عدم وجود الحساب خارج المصدر الذي فُحص.", ""]
    email_line = email_summary_line(data)
    if email_line:
        lines[:0] = [email_line, "",
                     "Email row: only the same address explicitly published in a retrieved public profile is shown.",
                     "غير مثبت = no public email link established; not proof of absence or account registration.",
                     "Profiles below are separate username/name search leads, not implied email links.", ""]
    findings = data.get("findings", [])
    grouped, seen = {}, set()
    for finding in findings:
        source = finding.get("source", "")
        attrs = finding.get("attributes", {})
        attrs = attrs if isinstance(attrs, dict) else {}
        derived = account_from_url(finding.get("url", ""))
        direct = source in PLATFORMS and (finding.get("kind") == "public_profile" or source in ("github", "gitlab", "keybase"))
        platform = source if direct else derived[0] if derived else ""
        username = attrs.get("username") if direct else derived[1] if derived else ""
        if not platform or not username:
            continue
        link = safe_url(finding.get("url", ""))
        key = (platform, str(username), link)
        if key in seen:
            continue
        seen.add(key)
        candidate = not direct or "candidate" in str(attrs.get("match_type", "")).lower()
        state = "candidate / مرشح من البحث" if candidate else "public profile / بيانات ملف عامة"
        grouped.setdefault(platform, []).append(f'{PLATFORMS[platform]}: {public_text(username)} [{state}]\n  {link}')
    coverage = {c.get("source"): c for c in data.get("coverage", []) if isinstance(c, dict)}
    checks = data.get("checks", [])
    for source, label in PLATFORMS.items():
        if source in grouped:
            lines.extend(grouped[source])
            continue
        if source not in coverage:
            continue
        row = coverage[source]
        states = {c.get("status") for c in checks if isinstance(c, dict) and c.get("source") == source}
        negative = row.get("status") == "not_found" or (row.get("status") == "ok" and not states)
        marker = "X" if negative else "?"
        description = "no returned accounts / لم يُرجع حسابات" if negative else f'{row.get("status", "unconfirmed")} / نتيجة غير حاسمة'
        lines.append(f'{label}: {marker} [{description}]')
        if row.get("detail"):
            lines.append("  " + public_text(row["detail"]))
    engines = {c.get("source") for c in checks if isinstance(c,dict) and str(c.get("source", "")).startswith("browser_")}
    for source in sorted(engines):
        states = {c.get("status") for c in checks if c.get("source") == source}
        count = sum(f.get("source") == source for f in findings)
        label = source.removeprefix("browser_").title()
        marker = str(count) + " search leads / روابط بحث" if count else "X" if states == {"not_found"} else "?"
        lines.append(f'{label}: {marker} [{", ".join(sorted(states))}]')
    for source in ("browser","brave"):
        if source in coverage:
            row=coverage[source]
            lines.append(f'{source.title()} search: {public_text(row.get("status", "unconfirmed"))}')
            lines.append("  " + public_text(row.get("detail", "")))
    if not grouped:
        lines.extend(["", "No account profiles were retrieved; review the source status above."])
    return "\n".join(lines) + "\n"


def write_accounts(data, directory, update_manifest=False):
    directory = Path(directory)
    path = directory / "accounts.txt"
    path.write_text(account_lines(data), encoding="utf-8")
    manifest = directory / "manifest.json"
    if update_manifest and manifest.is_file():
        hashes = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(hashes, dict):
            raise ValueError("Invalid manifest")
        # Preserve original evidence hashes: exporting a summary does not re-bless other files.
        hashes["accounts.txt"] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(hashes, indent=2), encoding="utf-8")
    return path.resolve()


def print_findings(data, limit=20):
    def display(value):
        encoding = getattr(sys.stdout,"encoding",None) or "utf-8"
        print(str(value).encode(encoding,errors="replace").decode(encoding))
    findings = sorted(data.get("findings", []), key=lambda f: f.get("score", 0), reverse=True)
    display(f"\nRetrieved leads / النتائج: {len(findings)} (showing up to {limit})")
    for f in findings[:limit]:
        display(f'[{public_text(f.get("source", ""))}] {public_text(f.get("title", ""))}')
        attrs = f.get("attributes", {})
        if isinstance(attrs, dict) and attrs.get("username"):
            display("  Username: " + public_text(attrs["username"]))
        link = safe_url(f.get("url", ""))
        if link:
            display("  " + link)
        if f.get("summary"):
            display("  " + public_text(f["summary"]).replace("\n", " ")[:600])
        display("  Unverified lead; check the source before attributing this account to the subject.")
