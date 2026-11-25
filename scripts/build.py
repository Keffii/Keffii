#!/usr/bin/env python3
"""Build the profile README and its light/dark SVG cards.

Standard library only. Content lives in profile.json; live numbers come from
the GitHub GraphQL API (token in GH_TOKEN or GITHUB_TOKEN).

    python scripts/build.py            # fetch live data, then build
    python scripts/build.py --gh       # fetch through the gh CLI (local dev)
    python scripts/build.py --offline  # rebuild from scripts/data.json
"""
import datetime as dt
import json
import math
import os
import subprocess
import sys
import urllib.request
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
DATA = ROOT / "scripts" / "data.json"

FONT = ("ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, "
        "'Liberation Mono', 'DejaVu Sans Mono', monospace")
CW = 0.61  # monospace glyph width as a fraction of font size (conservative)

THEMES = {
    "dark": {
        "bg": "#0b1015", "panel": "#0f161e", "border": "#1e2a36", "grid": "#16212c",
        "text": "#e6edf3", "muted": "#8a98a8", "accent": "#3ddc97", "warn": "#f5b041",
        "chip": "#15202b", "heat": ["#141d27", "#0e4a35", "#13734f", "#22a56f", "#3ddc97"],
    },
    "light": {
        "bg": "#ffffff", "panel": "#f7f9fb", "border": "#d6dde4", "grid": "#e8edf2",
        "text": "#1b2128", "muted": "#5a6672", "accent": "#0b8a5c", "warn": "#b36b00",
        "chip": "#eaf0f4", "heat": ["#e6ebf0", "#a6e3c6", "#5cc597", "#1f9e68", "#0b6e48"],
    },
}
W = 840          # full-width card
CARD_W = 410     # half-width project card


# ---------------------------------------------------------------- data

QUERY = """
query($login: String!) {
  user(login: $login) {
    createdAt
    repositories(ownerAffiliations: OWNER, privacy: PUBLIC, isFork: false, first: 100) {
      totalCount
      nodes { stargazerCount }
    }
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def fetch(login, via_gh=False):
    if via_gh:  # local dev: let the gh CLI handle auth and TLS
        body = json.loads(subprocess.run(
            ["gh", "api", "graphql", "-f", f"query={QUERY}", "-f", f"login={login}"],
            check=True, capture_output=True, text=True, encoding="utf-8").stdout)
    else:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            sys.exit("Set GH_TOKEN, or use --gh (gh CLI) or --offline")
        req = urllib.request.Request(
            "https://api.github.com/graphql",
            data=json.dumps({"query": QUERY, "variables": {"login": login}}).encode(),
            headers={"Authorization": f"bearer {token}", "User-Agent": "profile-cards"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.load(r)
    if body.get("errors"):
        sys.exit(f"GraphQL error: {body['errors']}")
    u = body["data"]["user"]
    cal = u["contributionsCollection"]["contributionCalendar"]
    days = [(d["date"], d["contributionCount"])
            for w in cal["weeks"] for d in w["contributionDays"]]
    return {
        "fetched": dt.date.today().isoformat(),
        "created": u["createdAt"][:10],
        "repos": u["repositories"]["totalCount"],
        "stars": sum(n["stargazerCount"] for n in u["repositories"]["nodes"]),
        "total": cal["totalContributions"],
        "days": days,
    }


def streaks(days):
    counts = [c for _, c in days]
    longest = run = 0
    for c in counts:
        run = run + 1 if c else 0
        longest = max(longest, run)
    current = 0
    i = len(counts) - 1
    if i >= 0 and counts[i] == 0:  # today not started yet: don't break the streak
        i -= 1
    while i >= 0 and counts[i]:
        current += 1
        i -= 1
    return current, longest


# ---------------------------------------------------------------- svg helpers

def svg(w, h, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" role="img"><title>{escape(title)}</title>'
            f'<style>text{{font-family:{FONT}}}</style>{body}</svg>\n')


def text(x, y, s, size, fill, weight=400, anchor="start", opacity=None):
    op = f' opacity="{opacity}"' if opacity is not None else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}"{op}>{escape(s)}</text>')


def panel(w, h, t, r=14):
    return (f'<rect x="0.5" y="0.5" width="{w-1}" height="{h-1}" rx="{r}" '
            f'fill="{t["panel"]}" stroke="{t["border"]}"/>')


def grid(x0, y0, x1, y1, step, t):
    lines = [f'<line x1="{x}" y1="{y0}" x2="{x}" y2="{y1}"/>' for x in range(x0, x1 + 1, step)]
    lines += [f'<line x1="{x0}" y1="{y}" x2="{x1}" y2="{y}"/>' for y in range(y0, y1 + 1, step)]
    return f'<g stroke="{t["grid"]}" stroke-width="1">{"".join(lines)}</g>'


def tw(s, size):
    return len(s) * size * CW


def wrap(s, size, width):
    limit = max(1, int(width / (size * CW)))
    lines, cur = [], ""
    for word in s.split():
        if cur and len(cur) + 1 + len(word) > limit:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}" if cur else word
    if cur:
        lines.append(cur)
    return lines


def chip_layout(items, size, width, gap=6, pad=9):
    """Return rows of (label, x, w) that fit in width."""
    rows, row, x = [], [], 0
    for it in items:
        w = tw(it, size) + pad * 2
        if row and x + w > width:
            rows.append(row)
            row, x = [], 0
        row.append((it, x, w))
        x += w + gap
    if row:
        rows.append(row)
    return rows


def chips(rows, x0, y0, size, t, row_h=26):
    out = []
    for r, row in enumerate(rows):
        y = y0 + r * row_h
        for label, x, w in row:
            out.append(f'<rect x="{x0+x:.1f}" y="{y}" width="{w:.1f}" height="20" rx="5" '
                       f'fill="{t["chip"]}" stroke="{t["border"]}"/>')
            out.append(text(x0 + x + w / 2, y + 14, label, size, t["text"], anchor="middle"))
    return "".join(out)


def fmt(n):
    return f"{n:,}"


# ---------------------------------------------------------------- cards

def card_hero(p, t):
    h = 252
    hero = p["hero"]
    b = [panel(W, h, t), grid(560, 30, 810, 210, 20, t)]
    b.append(text(36, 54, hero["prompt"] + " $ ./hello", 14, t["muted"]))
    b.append(text(36, 102, hero["title"], 40, t["text"], 700))
    for i, line in enumerate(wrap(hero["subtitle"], 15, 480)):
        b.append(text(36, 136 + i * 22, line, 15, t["muted"]))
    # status pill
    s = hero["status"]
    pw = tw(s, 12) + 40
    b.append(f'<rect x="36" y="{h-48}" width="{pw:.0f}" height="26" rx="13" fill="{t["chip"]}" stroke="{t["border"]}"/>')
    b.append(f'<circle cx="52" cy="{h-35}" r="4" fill="{t["accent"]}"><animate attributeName="opacity" values="1;.35;1" dur="2.4s" repeatCount="indefinite"/></circle>')
    b.append(text(64, h - 31, s, 12, t["text"]))
    # scope trace with a voltage dip
    pts = []
    for i in range(0, 251, 2):
        x = 560 + i
        y = 90 + 3 * math.sin(i / 6) + 1.5 * math.sin(i / 2.3)
        d = i - 165
        if -18 < d < 18:
            y += 58 * math.exp(-(d / 9) ** 2)
        pts.append(f"{x},{y:.1f}")
    b.append(f'<line x1="560" y1="128" x2="810" y2="128" stroke="{t["warn"]}" stroke-dasharray="4 4" opacity=".8"/>')
    b.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{t["accent"]}" stroke-width="2.2" stroke-linejoin="round"/>')
    b.append(f'<circle cx="725" cy="148" r="5" fill="none" stroke="{t["warn"]}" stroke-width="2"/>')
    b.append(text(737, 168, "dip 9.8 ms", 11, t["warn"]))
    b.append(text(564, 46, "CH1  3.3 V", 11, t["muted"]))
    b.append(text(806, 46, "100 Hz", 11, t["muted"], anchor="end"))
    b.append(text(806, 226, "trig ▲ baseline", 11, t["muted"], anchor="end"))
    alt = f'{hero["title"]} {hero["subtitle"]} Status: {s}.'
    return svg(W, h, "".join(b), alt), alt


def card_link(label, t):
    w, h = int(tw(label, 13) + 44), 36
    b = (f'<rect x="0.5" y="0.5" width="{w-1}" height="{h-1}" rx="18" fill="{t["panel"]}" stroke="{t["border"]}"/>'
         f'<text x="20" y="23" font-size="13" fill="{t["accent"]}">›</text>'
         + text(32, 23, label, 13, t["text"]))
    return svg(w, h, b, label)


def card_stats(d, t):
    h = 110
    cur, longest = streaks(d["days"])
    since = d["created"][:4]
    items = [
        (fmt(d["total"]), "contributions, 12 mo"),
        (f"{cur} d", "current streak"),
        (f"{longest} d", "longest streak"),
        (str(d["repos"]), "public repos"),
        (str(d["stars"]), "stars earned"),
    ]
    col = W / len(items)
    b = [panel(W, h, t)]
    for i, (num, label) in enumerate(items):
        cx = col * i + col / 2
        if i:
            b.append(f'<line x1="{col*i:.0f}" y1="26" x2="{col*i:.0f}" y2="{h-26}" stroke="{t["border"]}"/>')
        b.append(text(cx, 56, num, 28, t["text"], 700, "middle"))
        b.append(text(cx, 82, label, 12, t["muted"], anchor="middle"))
    alt = (f"On GitHub since {since}: {fmt(d['total'])} contributions in the last 12 months, "
           f"current streak {cur} days, longest streak {longest} days, "
           f"{d['repos']} public repos, {d['stars']} stars earned.")
    return svg(W, h, "".join(b), alt), alt


def card_heatmap(d, t):
    days = d["days"]
    cell, gap = 12, 3
    step = cell + gap
    weeks = []
    for date, c in days:
        wd = (dt.date.fromisoformat(date).weekday() + 1) % 7  # Sunday = 0
        if wd == 0 or not weeks:
            weeks.append([None] * 7)
        weeks[-1][wd] = (date, c)
    peak = max((c for _, c in days), default=0) or 1
    x0 = (W - len(weeks) * step + gap) / 2
    y0 = 74
    h = y0 + 7 * step + 40
    b = [panel(W, h, t)]
    b.append(text(x0, 40, f"{fmt(d['total'])} contributions in the last 12 months", 15, t["text"], 700))
    last_month, last_label = None, -9
    for wi, wk in enumerate(weeks):
        first = next(v for v in wk if v)[0]
        if first[5:7] != last_month:
            last_month = first[5:7]
            partial = wi == 0 and int(first[8:10]) > 14  # too little of that month shown
            if not partial and wi - last_label >= 3 and wi < len(weeks) - 2:
                label = dt.date.fromisoformat(first).strftime("%b")
                b.append(text(x0 + wi * step, y0 - 10, label, 11, t["muted"]))
                last_label = wi
        for di, cellv in enumerate(wk):
            if not cellv:
                continue
            date, c = cellv
            lvl = 0 if c == 0 else 1 + min(3, int(4 * math.sqrt(c / peak)))
            b.append(f'<rect x="{x0+wi*step:.1f}" y="{y0+di*step}" width="{cell}" height="{cell}" '
                     f'rx="2.5" fill="{t["heat"][lvl]}"><title>{c} on {date}</title></rect>')
    ly = h - 26
    lx = W - x0 - 5 * step - 34
    b.append(text(lx - 8, ly + 10, "less", 11, t["muted"], anchor="end"))
    for i, col in enumerate(t["heat"]):
        b.append(f'<rect x="{lx+i*step:.1f}" y="{ly}" width="{cell}" height="{cell}" rx="2.5" fill="{col}"/>')
    b.append(text(lx + 5 * step + 4, ly + 10, "more", 11, t["muted"]))
    alt = f"Contribution heatmap: {fmt(d['total'])} contributions in the last 12 months."
    return svg(W, int(h), "".join(b), alt), alt


def card_scope(d, t):
    last = d["days"][-30:]
    counts = [c for _, c in last]
    total, peak = sum(counts), max(counts, default=0)
    h = 190
    gx0, gx1, gy0, gy1 = 40, 800, 60, 160
    b = [panel(W, h, t), grid(gx0, gy0, gx1, gy1, 20, t)]
    b.append(text(40, 40, f"last 30 days · {fmt(total)} contributions", 15, t["text"], 700))
    b.append(text(800, 40, f"peak {peak}/day", 12, t["muted"], anchor="end"))
    top = max(peak, 1)
    n = max(len(counts) - 1, 1)
    pts = [(gx0 + (gx1 - gx0) * i / n, gy1 - (gy1 - gy0 - 6) * c / top) for i, c in enumerate(counts)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    b.append(f'<polygon points="{gx0},{gy1} {line} {gx1},{gy1}" fill="{t["accent"]}" opacity=".12"/>')
    b.append(f'<polyline points="{line}" fill="none" stroke="{t["accent"]}" stroke-width="2.2" stroke-linejoin="round"/>')
    if pts:
        x, y = pts[-1]
        b.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{t["accent"]}"/>')
    if last:
        b.append(text(gx0, h - 10, dt.date.fromisoformat(last[0][0]).strftime("%d %b"), 11, t["muted"]))
        b.append(text(gx1, h - 10, "today", 11, t["muted"], anchor="end"))
    alt = f"{fmt(total)} contributions in the last 30 days, peak {peak} in one day."
    return svg(W, h, "".join(b), alt), alt


def card_whoami(p, t):
    size, lh = 14, 23
    rows = []
    for cmd, out in p["whoami"]:
        rows.append(("cmd", cmd))
        rows += [("out", l) for l in wrap(out, size, W - 80)]
        rows.append(("gap", ""))
    rows.pop()
    h = 62 + len(rows) * lh + 22 - (lh // 2) * sum(1 for k, _ in rows if k == "gap")
    b = [panel(W, h, t)]
    b.append(f'<line x1="0" y1="38" x2="{W}" y2="38" stroke="{t["border"]}"/>')
    for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        b.append(f'<circle cx="{24+i*18}" cy="19" r="5.5" fill="{c}" opacity=".9"/>')
    b.append(text(W / 2, 24, "kevin@stockholm: ~", 12, t["muted"], anchor="middle"))
    y = 70
    for kind, s in rows:
        if kind == "gap":
            y += lh // 2
            continue
        if kind == "cmd":
            b.append(text(32, y, "$", size, t["accent"], 700))
            b.append(text(50, y, s, size, t["text"], 700))
        else:
            b.append(text(50, y, s, size, t["muted"]))
        y += lh
    b.append(f'<rect x="32" y="{y-14}" width="9" height="17" fill="{t["accent"]}">'
             f'<animate attributeName="opacity" values="1;0;1" dur="1.1s" repeatCount="indefinite"/></rect>')
    alt = "whoami: " + " ".join(out for _, out in p["whoami"])
    return svg(W, h, "".join(b), alt), alt


def card_label(s, t):
    h = 46
    b = (text(4, 30, "//", 15, t["accent"], 700) + text(30, 30, s, 15, t["text"], 700)
         + f'<line x1="{36+tw(s,15):.0f}" y1="25" x2="{W-4}" y2="25" stroke="{t["border"]}"/>')
    return svg(W, h, b, s)


def project_layout(pr):
    desc = wrap(pr["desc"], 13, CARD_W - 48)
    rows = chip_layout(pr["stack"], 11, CARD_W - 48)
    return desc, rows


def card_project(pr, t, h):
    desc, rows = project_layout(pr)
    b = [panel(CARD_W, h, t)]
    b.append(text(24, 40, pr["name"], 19, t["text"], 700))
    b.append(text(CARD_W - 24, 40, "↗", 16, t["muted"], anchor="end"))
    b.append(text(24, 62, pr["tag"], 12, t["accent"]))
    for i, line in enumerate(desc):
        b.append(text(24, 92 + i * 19, line, 13, t["muted"]))
    b.append(chips(rows, 24, h - 22 - len(rows) * 26 + 6, 11, t))
    alt = f'{pr["name"]} ({pr["tag"]}): {pr["desc"]} Built with {", ".join(pr["stack"])}.'
    return svg(CARD_W, h, "".join(b), alt), alt


def card_stack(p, t):
    label_w, size = 120, 12
    blocks, y = [], 34
    for g in p["stack"]:
        rows = chip_layout(g["items"], size, W - label_w - 56, gap=7, pad=10)
        blocks.append((g["group"], rows, y))
        y += len(rows) * 28 + 18
    h = y + 6
    b = [panel(W, h, t)]
    for group, rows, gy in blocks:
        b.append(text(28, gy + 14, group, 13, t["accent"], 700))
        for r, row in enumerate(rows):
            ry = gy + r * 28
            for label, x, w in row:
                x += label_w + 28
                b.append(f'<rect x="{x:.1f}" y="{ry}" width="{w:.1f}" height="21" rx="5" '
                         f'fill="{t["chip"]}" stroke="{t["border"]}"/>')
                b.append(text(x + w / 2, ry + 15, label, size, t["text"], anchor="middle"))
    alt = "Tech. " + " ".join(f'{g["group"].capitalize()}: {", ".join(g["items"])}.' for g in p["stack"])
    return svg(W, h, "".join(b), alt), alt


# ---------------------------------------------------------------- readme

def pic(name, alt, width="100%", href=None):
    tag = (f'<picture><source media="(prefers-color-scheme: dark)" srcset="assets/{name}-dark.svg">'
           f'<img src="assets/{name}-light.svg" width="{width}" alt="{escape(alt)}"></picture>')
    return f'<a href="{href}">{tag}</a>' if href else tag


def build(p, d):
    ASSETS.mkdir(exist_ok=True)
    alts = {}

    def emit(name, fn):
        for theme, t in THEMES.items():
            out = fn(t)
            s, alt = out if isinstance(out, tuple) else (out, None)
            (ASSETS / f"{name}-{theme}.svg").write_text(s, encoding="utf-8", newline="\n")
        alts[name] = alt

    emit("hero", lambda t: card_hero(p, t))
    for l in p["links"]:
        emit(f"link-{l['id']}", lambda t, l=l: card_link(l["label"], t))
    emit("stats", lambda t: card_stats(d, t))
    emit("heatmap", lambda t: card_heatmap(d, t))
    emit("scope", lambda t: card_scope(d, t))
    emit("whoami", lambda t: card_whoami(p, t))
    for name, label in (("label-build", "what I build"), ("label-stack", "stack"), ("label-activity", "activity")):
        emit(name, lambda t, label=label: card_label(label, t))
    ph = max(  # equal height for every project card so the grid lines up
        110 + len(desc) * 19 + len(rows) * 26
        for desc, rows in map(project_layout, p["projects"]))
    for pr in p["projects"]:
        emit(f"project-{pr['id']}", lambda t, pr=pr: card_project(pr, t, ph))
    emit("stack", lambda t: card_stack(p, t))

    links = "\n".join(
        f'<a href="{l["url"]}"><picture><source media="(prefers-color-scheme: dark)" '
        f'srcset="assets/link-{l["id"]}-dark.svg"><img src="assets/link-{l["id"]}-light.svg" '
        f'height="36" alt="{escape(l["label"])}"></picture></a>' for l in p["links"])
    projects = "\n".join(pic(f"project-{pr['id']}", alts[f"project-{pr['id']}"], "49%", pr["url"])
                         for pr in p["projects"])
    readme = f"""{pic("hero", alts["hero"])}

<p align="center">
{links}
</p>

{pic("whoami", alts["whoami"])}

{pic("label-build", "What I build")}

<p align="center">
{projects}
</p>

{pic("label-stack", "Stack")}

{pic("stack", alts["stack"])}

{pic("label-activity", "Activity")}

{pic("stats", alts["stats"])}

{pic("heatmap", alts["heatmap"])}

{pic("scope", alts["scope"])}

<p align="center"><sub>Cards drawn by <a href="scripts/build.py">scripts/build.py</a> and redrawn every night by a GitHub Action · light and dark themes</sub></p>
"""
    (ROOT / "README.md").write_text(readme, encoding="utf-8", newline="\n")


def main():
    p = json.loads((ROOT / "profile.json").read_text(encoding="utf-8"))
    if "--offline" in sys.argv:
        d = json.loads(DATA.read_text(encoding="utf-8"))
    else:
        d = fetch(p["login"], via_gh="--gh" in sys.argv)
        DATA.write_text(json.dumps(d, indent=1), encoding="utf-8", newline="\n")
    build(p, d)
    print(f"built README.md and {len(list(ASSETS.glob('*.svg')))} SVGs "
          f"({d['total']} contributions, data from {d['fetched']})")


if __name__ == "__main__":
    main()
