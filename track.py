#!/usr/bin/env python3
"""Rebuild the outreach table from GitHub, live.

Every row is fetched at generation time and the file is REBUILT, never appended to: a PR that is
merged since the last run says merged here, and an entry whose repository disappears disappears too.

What counts as outreach is the rule the swarm already enforces everywhere else: the work has to have
left the house. An issue or PR whose repository is owned by an account we control reached no
maintainer and is a note to self, so it is excluded here exactly as `rai_scope` excludes it from the
distribution ledger. (35 issues were once opened on our own forks and reported as outreach.)
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

API = "https://api.github.com"

# The accounts we control. `dhyabi2` is the owner's own account and is used as a fallback when the
# agent account cannot post; work on a repo under either name is ours, not outreach.
OWNED_GITHUB = {"pandeveloper001", "dhyabi2"}

# Whose submissions to track. Both accounts post outreach; the table says which one, because that
# is a real difference to a maintainer reading the thread.
AUTHORS = ("PANDeveloper001", "dhyabi2")

# Merged is a distinct outcome from closed and must never be flattened into it: a merged PR is the
# only state in this table that means someone else accepted our work.
STATE_ORDER = {"merged": 0, "open": 1, "closed": 2}
STATE_MARK = {"merged": "merged", "open": "open", "closed": "closed"}


def _token() -> str | None:
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        v = os.environ.get(name)
        if v:
            return v
    return None


def fetch(path: str, token: str | None = None) -> dict:
    req = urllib.request.Request(
        f"{API}{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "nano-swarm-outreach-tracker",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            # 403/429 from the search API is a rate limit, not a broken query: wait and retry rather
            # than publish a table with half the rows missing.
            if e.code in (403, 429) and attempt < 2:
                time.sleep(20 * (attempt + 1))
                continue
            raise
        except urllib.error.URLError:
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise
    raise RuntimeError("unreachable")


def repo_of(item: dict) -> str:
    """owner/repo from a search result, which carries only repository_url."""
    return item.get("repository_url", "").split("/repos/", 1)[-1]


def is_ours(full_name: str) -> bool:
    owner = full_name.split("/", 1)[0].strip().lower()
    return owner in OWNED_GITHUB


def state_of(item: dict) -> str:
    pr = item.get("pull_request") or {}
    if pr.get("merged_at"):
        return "merged"
    return "open" if item.get("state") == "open" else "closed"


def rows_from(items: list[dict], author: str) -> list[dict]:
    out = []
    for it in items:
        repo = repo_of(it)
        if not repo or is_ours(repo):
            continue
        out.append(
            {
                "repo": repo,
                "number": it.get("number"),
                "title": (it.get("title") or "").strip(),
                "kind": "pr" if it.get("pull_request") else "issue",
                "state": state_of(it),
                "url": it.get("html_url", ""),
                "author": author,
                "created_at": it.get("created_at", ""),
                "updated_at": it.get("updated_at", ""),
                "comments": it.get("comments", 0),
            }
        )
    return out


def collect(token: str | None = None, authors: tuple[str, ...] = AUTHORS) -> list[dict]:
    rows: list[dict] = []
    for author in authors:
        page = 1
        while page <= 10:  # 1000 results is the search API's hard ceiling
            q = f"author:{author}+type:issue"
            data = fetch(f"/search/issues?q={q}&per_page=100&page={page}&sort=created&order=desc", token)
            items = data.get("items", [])
            rows.extend(rows_from(items, author))
            if len(items) < 100:
                break
            page += 1
    seen, uniq = set(), []
    for r in sorted(rows, key=lambda r: (STATE_ORDER.get(r["state"], 9), r["repo"], r["number"] or 0)):
        key = r["url"]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq


def summarise(rows: list[dict]) -> dict:
    s = {"total": len(rows), "merged": 0, "open": 0, "closed": 0, "repos": 0, "answered": 0}
    for r in rows:
        s[r["state"]] = s.get(r["state"], 0) + 1
        # A maintainer replying is the only evidence in this data that a person actually read it.
        if r.get("comments", 0) > 0:
            s["answered"] += 1
    s["repos"] = len({r["repo"] for r in rows})
    return s


def render(rows: list[dict], generated: str) -> str:
    s = summarise(rows)
    lines = [
        "# Outreach tracker",
        "",
        "Every issue and pull request the Nano swarm has opened on **someone else's** repository, with the state",
        "GitHub reports right now. Rebuilt on a schedule by [`track.py`](track.py) — never appended to, so a PR that",
        "gets merged says merged here without anyone editing the table.",
        "",
        f"**{s['total']}** submissions across **{s['repos']}** repositories · "
        f"**{s['merged']}** merged · **{s['open']}** open · **{s['closed']}** closed · "
        f"**{s['answered']}** have at least one reply.",
        "",
        f"_Generated {generated}._",
        "",
        "Work on a repository under an account we control is not outreach and never appears here: it reaches no",
        "maintainer. 35 issues were once opened on our own forks of other people's projects and reported as outreach,",
        "which is the mistake this table exists to make impossible to repeat.",
        "",
        "| State | Repository | # | Title | Kind | Replies | Opened | By |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        title = r["title"].replace("|", "\\|")
        if len(title) > 90:
            title = title[:87] + "..."
        opened = (r["created_at"] or "")[:10]
        lines.append(
            f"| {STATE_MARK.get(r['state'], r['state'])} "
            f"| [{r['repo']}](https://github.com/{r['repo']}) "
            f"| [#{r['number']}]({r['url']}) | {title} | {r['kind']} "
            f"| {r.get('comments', 0)} | {opened} | {r['author']} |"
        )
    if not rows:
        lines.append("| — | _nothing has left the house yet_ | | | | | | |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    token = _token()
    if not token:
        print("warning: no GITHUB_TOKEN; the search API will rate-limit quickly", file=sys.stderr)
    rows = collect(token)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "data.json"), "w") as f:
        json.dump({"generated": generated, "summary": summarise(rows), "rows": rows}, f, indent=2)
        f.write("\n")
    with open(os.path.join(here, "README.md"), "w") as f:
        f.write(render(rows, generated))
    print(f"{len(rows)} rows across {len({r['repo'] for r in rows})} repositories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
