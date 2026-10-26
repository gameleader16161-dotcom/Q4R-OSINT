import csv
import hashlib
import html
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import asdict

from .core import safe_url, locked
from .results import write_accounts, email_summary_line


STYLE = """
:root{color-scheme:dark}body{font:16px system-ui;background:#0c1422;color:#e4edf7;margin:0}
main{max-width:1160px;margin:auto;padding:36px}h1{font-size:42px;margin-bottom:4px;color:#57e8cb}
.muted{color:#a9b7cb}section{background:#142135;border:1px solid #2a3b53;border-radius:14px;padding:22px;margin:22px 0}
a{color:#7ad9ff;overflow-wrap:anywhere}input{background:#0c1422;color:white;padding:12px;border:1px solid #6a839e;border-radius:8px;width:90%}
table{width:100%;border-collapse:collapse}th,td{text-align:start;padding:12px;border-bottom:1px solid #314157;vertical-align:top}
details{padding:10px}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere}svg{width:100%;max-height:700px}text{fill:#e4edf7;font:12px system-ui}
.badge{display:inline-block;background:#244b53;padding:4px 9px;border-radius:8px}.finding[hidden]{display:none}
"""


def escape(x):
    return html.escape(str(x), quote=True)


def link(url, label):
    url = safe_url(url)
    return f'<a href="{escape(url)}" rel="noreferrer noopener" target="_blank">{escape(label)}</a>' if url else escape(label)


@locked
def write_report(case):
    data = case.data()
    directory = case.directory
    (directory / "report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    write_accounts(data, directory)
    (directory / "searches.txt").write_text(
        "MANUAL SEARCH LINKS — these are research tasks, not retrieved findings.\n\n" +
        "\n\n".join(p["query"] + "\n" + p["url"] for p in case.plan), encoding="utf-8")
    with (directory / "findings.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "source", "title", "url", "summary", "score", "signals", "evidence_id", "query"])
        for x in case.findings:
            values = [x.id, x.source, x.title, x.url, x.summary, x.score, "; ".join(x.signals), x.evidence_id, x.query]
            # Prevent spreadsheet formula injection, including leading whitespace.
            writer.writerow(["'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v for v in values])
    write_graph(case)
    cards = []
    for x in sorted(case.findings, key=lambda item: item.score, reverse=True):
        cards.append(f'<section class="finding"><span class="badge">{x.id} · {escape(x.source)} · evidence score {x.score}/90</span>'
                     f'<h3 dir="auto">{link(x.url, x.title)}</h3><p dir="auto">{escape(x.summary)}</p>'
                     f'<p>{escape("; ".join(x.signals))}</p><p class="muted">{escape(x.observed_at)} · Evidence {escape(x.evidence_id)}</p>'
                     f'<details><summary>Query and public fields</summary><pre>{escape(x.query)}\n{escape(json.dumps(x.attributes, ensure_ascii=False, indent=2))}</pre></details></section>')
    coverage = "".join(f'<tr><td>{escape(c["source"])}</td><td>{escape(c["status"])}</td><td>{escape(c["detail"])}</td></tr>' for c in case.coverage)
    checks = "".join(f'<tr><td>{escape(c["source"])}</td><td>{escape(c["status"])}</td>'
                     f'<td>{link(c["url"], c["query"] or c["url"])}</td><td>{escape(c["detail"])}</td>'
                     f'<td>{escape(c["evidence_id"])}</td></tr>' for c in case.checks)
    check_section = ('<section><h2>Direct profile checks</h2><p>Found means the source returned profile data; '
                     'it does not prove ownership. Blocked/unconfirmed are inconclusive, not absent accounts.</p>'
                     '<table><tr><th>Platform</th><th>Status</th><th>Input / URL</th><th>Evidence</th><th>ID</th></tr>' + checks + '</table></section>') if checks else ''
    plan = "".join(f'<tr><td>{link(p["url"], p["query"])}</td><td>{escape(p["status"])}</td></tr>' for p in case.plan)
    graph = graph_svg(case)
    web_coverage = next((c for c in case.coverage if c["source"] == "brave"), None)
    notice = ""
    if web_coverage and web_coverage["status"] in ("skipped", "error"):
        notice = ('<section role="status"><h2>Web index coverage</h2><p>' +
                  ('Brave indexed web search did not run. ' if web_coverage["status"] == "skipped" else
                   'Brave indexed web search encountered an error and may be incomplete. ') +
                  escape(web_coverage["detail"]) + '</p><p>Use the search plan below or searches.txt for manual research. '
                  'No retrieved leads does not prove that no accounts exist.</p></section>')
    open_plan = ' open' if not case.findings else ''
    input_summary = email_summary_line(data) or "Email: not supplied / لم يتم إدخال بريد"
    if case.target.country:
        input_summary += "\nCountry keyword (supplied, unverified): " + case.target.country
    if case.target.aliases:
        input_summary += "\nAdditional supplied usernames (ownership unverified): " + ", ".join(case.target.aliases)
    if len(data["name_search"]["forms"]) > 1:
        input_summary += "\nFull-name spellings planned (not verified aliases):\n" + "\n".join(
            row["value"] + " [" + row["origin"] + "]" for row in data["name_search"]["forms"])
    photo_section = ""
    if any(row.get("file") == "attachments/reference.png" for row in case.attachments):
        photo_section = '<section><h2>Reference attachment</h2><img src="attachments/reference.png" alt="User supplied visual reference" style="max-width:100%;max-height:300px;border-radius:12px"><p>Local attachment only. No face recognition, account matching or image upload was performed.</p></section>'
    document = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self'; base-uri 'none'; form-action 'none'">
<title>Q4R · Investigation report</title><style>{STYLE}</style><main><h1>Q4R</h1><p class="muted">Public-source investigation · {escape(case.started_at)}</p>
<section><h2 dir="auto">{escape(case.target.label())}</h2><pre dir="auto">{escape(input_summary)}</pre>
<p>{len(case.findings)} retrieved leads · {len(case.requests)} recorded HTTP attempts · {len(case.plan)} planned searches</p>
<p>{escape(data["interpretation"])}</p><p>Search links are research tasks, not confirmed accounts or retrieved evidence.</p></section>
{notice}
{photo_section}
<section><h2>Source coverage</h2><table><tr><th>Source</th><th>Status</th><th>Detail</th></tr>{coverage}</table></section>
{check_section}
<section><h2>Evidence relationships</h2><p>Edges mean “returned for this input”, not “belongs to this person”. First 24 leads displayed; graph.graphml contains all leads.</p>{graph}</section>
<h2>Retrieved leads</h2><input id="filter" aria-label="Filter leads" placeholder="Filter source, title or evidence…">{''.join(cards) or '<section>No leads retrieved. Check source coverage and manual searches.</section>'}
<section><details{open_plan}><summary>Search plan · {len(case.plan)} tasks</summary><table>{plan}</table></details></section>
<p class="muted">Evidence bodies and SHA-256 hashes are stored alongside this report. Reports contain personal data; keep them private.</p>
</main><script>document.getElementById('filter').addEventListener('input',function(){{for(const item of document.querySelectorAll('.finding'))item.hidden=!item.textContent.toLowerCase().includes(this.value.toLowerCase())}})</script></html>'''
    (directory / "report.html").write_text(document, encoding="utf-8")
    manifest = {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(directory.rglob("*")) if p.is_file() and p.name != "manifest.json"}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def graph_svg(case):
    nodes = case.findings[:24]
    parts = ['<svg viewBox="0 0 1000 700" role="img" aria-label="Unverified evidence leads">']
    for i, x in enumerate(nodes):
        angle = i * 2 * math.pi / max(len(nodes), 1)
        px, py = 500 + 370 * math.cos(angle), 350 + 275 * math.sin(angle)
        parts.append(f'<line x1="500" y1="350" x2="{px:.1f}" y2="{py:.1f}" stroke="#355168"/>')
        parts.append(f'<g><title>{escape(x.title)}</title><circle cx="{px:.1f}" cy="{py:.1f}" r="19" fill="#226d78"/><text x="{px:.1f}" y="{py+36:.1f}" text-anchor="middle">{escape(x.id + " " + x.source)}</text></g>')
    parts.append('<circle cx="500" cy="350" r="45" fill="#244f69"/><text x="500" y="355" text-anchor="middle">INPUT</text></svg>')
    return "".join(parts)


def write_graph(case):
    ns = "http://graphml.graphdrawing.org/xmlns"
    ET.register_namespace("", ns)
    root = ET.Element("{" + ns + "}graphml")
    for key in ("label", "url", "relation", "evidence"):
        ET.SubElement(root, "{" + ns + "}key", {"id": key, "for": "all", "attr.name": key, "attr.type": "string"})
    graph = ET.SubElement(root, "{" + ns + "}graph", {"id": "Q4R", "edgedefault": "directed"})
    node = ET.SubElement(graph, "{" + ns + "}node", {"id": "input"})
    ET.SubElement(node, "{" + ns + "}data", {"key": "label"}).text = case.target.label()
    external = set()
    for x in case.findings:
        node = ET.SubElement(graph, "{" + ns + "}node", {"id": x.id})
        for key, value in (("label", x.title), ("url", x.url), ("evidence", x.evidence_id)):
            ET.SubElement(node, "{" + ns + "}data", {"key": key}).text = value
        edge = ET.SubElement(graph, "{" + ns + "}edge", {"source": "input", "target": x.id})
        ET.SubElement(edge, "{" + ns + "}data", {"key": "relation"}).text = "unverified search lead"
        for item in x.attributes.get("links", []):
            if not isinstance(item, dict):
                continue
            url = safe_url(item.get("url", ""))
            if not url:
                continue
            key = "url_" + hashlib.sha256(url.encode()).hexdigest()[:20]
            if key not in external:
                external.add(key)
                linked = ET.SubElement(graph, "{" + ns + "}node", {"id": key})
                ET.SubElement(linked, "{" + ns + "}data", {"key": "label"}).text = url
                ET.SubElement(linked, "{" + ns + "}data", {"key": "url"}).text = url
            relation = ET.SubElement(graph, "{" + ns + "}edge", {"source": x.id, "target": key})
            ET.SubElement(relation, "{" + ns + "}data", {"key": "relation"}).text = item.get("relation", "self-published link; unverified ownership")
            ET.SubElement(relation, "{" + ns + "}data", {"key": "evidence"}).text = x.evidence_id
    ET.ElementTree(root).write(case.directory / "graph.graphml", encoding="utf-8", xml_declaration=True)
