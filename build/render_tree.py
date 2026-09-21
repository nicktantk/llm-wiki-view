"""Render the published llm-wiki tree as one static HTML page.

Reads nicktantk/llm-wiki over the public GitCode API — no token, no clone — so it works
from anywhere and can be pointed at any ref. The output is a self-contained page suitable
for an artifact, GitHub Pages, or just opening locally.

    python render_tree.py [--ref main] [--out tree.html] [--json tree.json]
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import re
import sys
from urllib.parse import quote

import httpx

API = "https://api.gitcode.com/api/v5/repos/nicktantk/llm-wiki"
WEB = "https://gitcode.com/nicktantk/llm-wiki/blob"


def get_json(client: httpx.Client, path: str, ref: str):
    r = client.get(f"{API}/contents/{quote(path)}", params={"ref": ref},
                   headers={"Accept": "application/json"}, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def read_file(client: httpx.Client, path: str, ref: str) -> str | None:
    data = get_json(client, path, ref)
    if not isinstance(data, dict) or "content" not in data:
        return None
    return base64.b64decode(data["content"]).decode("utf-8")


def list_dir(client: httpx.Client, path: str, ref: str) -> list[dict]:
    data = get_json(client, path, ref)
    return data if isinstance(data, list) else []


FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.S)


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Minimal frontmatter reader: flat `key: value` pairs, plus one level of nesting."""
    m = FRONTMATTER.match(text or "")
    if not m:
        return {}, text or ""
    meta: dict = {}
    current_key = None
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indented = line[:1] in (" ", "\t")
        if ":" not in line:
            if indented and current_key and line.strip().startswith("- "):
                meta.setdefault(current_key, []).append(line.strip()[2:].strip())
            continue
        key, _, value = line.partition(":")
        value = value.strip().strip("'\"")
        if indented and current_key:
            if isinstance(meta.get(current_key), dict):
                meta[current_key][key.strip()] = value
            continue
        key = key.strip()
        current_key = key
        meta[key] = value if value else {}
    return meta, m.group(2)


def table_rows(text: str) -> list[list[str]]:
    """Rows of the one index-table format the wiki uses."""
    rows, seen_sep = [], False
    for line in (text or "").splitlines():
        if line.startswith("|---"):
            seen_sep = True
            continue
        if seen_sep and line.startswith("|"):
            rows.append([c.strip() for c in line.strip("|").split("|")])
    return rows


def collect_local(root: str) -> dict:
    """Same shape as collect(), read from a checkout instead of the API."""
    import pathlib

    base = pathlib.Path(root)
    tree: dict = {"ref": "local", "projects": [], "incidents": [], "knowledge": {}}

    def text_of(rel: str) -> str:
        path = base / rel
        return path.read_text(encoding="utf-8") if path.exists() else ""

    for row in table_rows(text_of("wiki/index.md")):
        tree["projects"].append({"name": row[0], "description": row[1] if len(row) > 1 else "",
                                 "updated_by": row[3] if len(row) > 3 else "", "findings": []})

    for project in tree["projects"]:
        findings_dir = base / "wiki" / project["name"] / "findings"
        if not findings_dir.exists():
            continue
        for path in sorted(findings_dir.glob("*.md")):
            if path.name == "index.md":
                continue
            meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
            project["findings"].append({
                "id": path.stem, "meta": meta, "body": body,
                "path": f"wiki/{project['name']}/findings/{path.name}",
            })

    raw_dir = base / "wiki" / "raw" / "2026-09"
    for path in sorted(raw_dir.glob("*.md")) if raw_dir.exists() else []:
        meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
        tree["incidents"].append({"id": path.stem, "meta": meta, "body": body,
                                  "path": f"wiki/raw/2026-09/{path.name}"})

    for zone in ("patterns", "playbooks", "principles"):
        tree["knowledge"][zone] = table_rows(text_of(f"wiki/{zone}/index.md"))
    return tree


def collect(ref: str) -> dict:
    with httpx.Client(follow_redirects=True) as client:
        tree: dict = {"ref": ref, "projects": [], "incidents": [], "knowledge": {}}

        for row in table_rows(read_file(client, "wiki/index.md", ref) or ""):
            tree["projects"].append({"name": row[0], "description": row[1] if len(row) > 1 else "",
                                     "updated_by": row[3] if len(row) > 3 else "",
                                     "findings": []})

        for project in tree["projects"]:
            root = f"wiki/{project['name']}/findings"
            for item in list_dir(client, root, ref):
                name = item.get("name", "")
                if not name.endswith(".md") or name == "index.md":
                    continue
                text = read_file(client, f"{root}/{name}", ref) or ""
                meta, body = split_frontmatter(text)
                project["findings"].append({
                    "id": name[:-3], "meta": meta, "body": body,
                    "path": f"{root}/{name}",
                })

        for item in list_dir(client, "wiki/raw/2026-09", ref):
            name = item.get("name", "")
            if not name.endswith(".md"):
                continue
            path = f"wiki/raw/2026-09/{name}"
            meta, body = split_frontmatter(read_file(client, path, ref) or "")
            tree["incidents"].append({"id": name[:-3], "meta": meta, "body": body, "path": path})

        for zone in ("patterns", "playbooks", "principles"):
            tree["knowledge"][zone] = table_rows(read_file(client, f"wiki/{zone}/index.md", ref) or "")

        return tree


def summary(body: str) -> str:
    """The Summary section's prose, or the first paragraph."""
    m = re.search(r"##\s*(?:Summary|Claim)\s*\n+(.+?)(?=\n##|\Z)", body or "", re.S)
    text = (m.group(1) if m else (body or "")).strip()
    return re.sub(r"\s+", " ", text)[:600]


def esc(value) -> str:
    return html.escape(str(value or ""))


def render(tree: dict) -> str:
    ref = tree["ref"]
    incidents_by_finding: dict[str, list[dict]] = {}
    for incident in tree["incidents"]:
        incidents_by_finding.setdefault(str(incident["meta"].get("finding") or ""), []).append(incident)

    n_findings = sum(len(p["findings"]) for p in tree["projects"])
    n_open = sum(1 for i in tree["incidents"] if not i["meta"].get("superseded_by"))
    n_closed = len(tree["incidents"]) - n_open

    parts = [f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>llm-wiki tree</title>
<style>
 :root {{ color-scheme: light dark; --bg:#fbfbfa; --fg:#1a1a18; --muted:#6b6b66;
          --line:#e0e0da; --card:#fff; --accent:#8a5a2b; }}
 @media (prefers-color-scheme: dark) {{ :root {{ --bg:#191917; --fg:#eceae4; --muted:#9a978e;
          --line:#33322e; --card:#211f1d; --accent:#d8a05c; }} }}
 * {{ box-sizing:border-box; }}
 body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; }}
 .wrap {{ max-width:1000px; margin:0 auto; padding:32px 16px 72px; }}
 h1 {{ font-size:1.6rem; margin:0 0 4px; }}
 h2 {{ font-size:1.15rem; margin:36px 0 12px; padding-bottom:6px; border-bottom:1px solid var(--line); }}
 .sub {{ color:var(--muted); margin:0 0 20px; }}
 .stats {{ display:flex; flex-wrap:wrap; gap:10px; margin:18px 0 8px; }}
 .stat {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 14px; min-width:110px; }}
 .stat b {{ display:block; font-size:1.5rem; line-height:1.2; }}
 .stat span {{ color:var(--muted); font-size:.82rem; }}
 details {{ background:var(--card); border:1px solid var(--line); border-radius:10px; margin:10px 0; padding:12px 14px; }}
 summary {{ cursor:pointer; font-weight:600; }}
 .claim {{ margin:10px 0 6px; }}
 .meta {{ color:var(--muted); font-size:.85rem; display:flex; flex-wrap:wrap; gap:8px; margin-top:8px; }}
 .tag {{ border:1px solid var(--line); border-radius:999px; padding:1px 9px; }}
 .inc {{ border-left:2px solid var(--line); margin:10px 0 0 4px; padding:2px 0 2px 12px; }}
 .inc p {{ margin:4px 0; }}
 a {{ color:var(--accent); }}
 code {{ background:rgba(128,128,128,.14); padding:1px 5px; border-radius:4px; font-size:.88em; }}
 .empty {{ color:var(--muted); font-style:italic; }}
 table {{ border-collapse:collapse; width:100%; font-size:.9rem; }}
 td,th {{ border-bottom:1px solid var(--line); padding:7px 8px; text-align:left; vertical-align:top; }}
 .scroll {{ overflow-x:auto; }}
</style></head><body><div class="wrap">
<h1>llm-wiki</h1>
<p class="sub">The tree at <code>{esc(ref)}</code> on
<a href="https://gitcode.com/nicktantk/llm-wiki">gitcode.com/nicktantk/llm-wiki</a>.</p>
<div class="stats">
 <div class="stat"><b>{len(tree['projects'])}</b><span>projects</span></div>
 <div class="stat"><b>{n_findings}</b><span>findings</span></div>
 <div class="stat"><b>{n_open}</b><span>incidents open</span></div>
 <div class="stat"><b>{n_closed}</b><span>superseded</span></div>
 <div class="stat"><b>{len(tree['knowledge'].get('patterns',[]))}</b><span>patterns</span></div>
 <div class="stat"><b>{len(tree['knowledge'].get('playbooks',[]))}</b><span>playbooks</span></div>
</div>"""]

    for project in tree["projects"]:
        parts.append(f'<h2>{esc(project["name"])}</h2>')
        if not project["findings"]:
            parts.append('<p class="empty">No findings yet.</p>')
        for finding in sorted(project["findings"], key=lambda f: f["id"]):
            meta = finding["meta"]
            claim = meta.get("claim") or summary(finding["body"])
            topic = meta.get("topic")
            tags = [f'<span class="tag">{esc(meta.get("category"))}</span>' if meta.get("category") else "",
                    f'<span class="tag">{esc(meta.get("severity"))}</span>' if meta.get("severity") else "",
                    f'<span class="tag">topic: {esc(topic)}</span>' if topic and topic != "null" else
                    '<span class="tag">unclustered</span>']
            cited = incidents_by_finding.get(finding["id"], [])
            parts.append(f"""<details><summary>{esc(finding['id'])}</summary>
<p class="claim">{esc(claim)}</p>
<div class="meta">{''.join(t for t in tags if t)}
<span><a href="{WEB}/{esc(ref)}/{esc(finding['path'])}">source</a></span></div>""")
            for incident in cited:
                imeta = incident["meta"]
                state = "superseded" if imeta.get("superseded_by") else "open"
                parts.append(f"""<div class="inc"><p><b>{esc(imeta.get('title'))}</b></p>
<p class="meta"><span class="tag">incident · {state}</span>
<span>{esc(imeta.get('submitted_by'))}</span><span>{esc(imeta.get('submitted_at'))}</span>
<span><a href="{WEB}/{esc(ref)}/{esc(incident['path'])}">{esc(incident['id'])}</a></span></p>
<p>{esc(summary(incident['body']))}</p></div>""")
            parts.append("</details>")

    orphans = [i for i in tree["incidents"] if not i["meta"].get("finding")]
    if orphans:
        parts.append(f'<h2>Incidents not yet synthesized ({len(orphans)})</h2>')
        for incident in orphans:
            parts.append(f"""<div class="inc"><p><b>{esc(incident['meta'].get('title'))}</b></p>
<p class="meta"><span>{esc(incident['meta'].get('project'))}</span>
<span><a href="{WEB}/{esc(ref)}/{esc(incident['path'])}">{esc(incident['id'])}</a></span></p></div>""")

    for zone, rows in tree["knowledge"].items():
        parts.append(f"<h2>{zone}</h2>")
        if not rows:
            parts.append('<p class="empty">Empty.</p>')
            continue
        parts.append('<div class="scroll"><table><tr><th>Concept</th><th>Description</th></tr>')
        for row in rows:
            parts.append(f"<tr><td>{esc(row[0])}</td><td>{esc(row[1] if len(row) > 1 else '')}</td></tr>")
        parts.append("</table></div>")

    parts.append("</div></body></html>")
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", default="main")
    parser.add_argument("--local", help="render from a checkout instead of the API")
    parser.add_argument("--out", default="tree.html")
    parser.add_argument("--json")
    args = parser.parse_args()

    tree = collect_local(args.local) if args.local else collect(args.ref)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(render(tree))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(tree, handle, indent=2, ensure_ascii=False)
    findings = sum(len(p["findings"]) for p in tree["projects"])
    print(f"{args.out}: {len(tree['projects'])} project(s), {findings} finding(s), "
          f"{len(tree['incidents'])} incident(s) at {args.ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
