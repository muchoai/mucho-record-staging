#!/usr/bin/env python3
"""Switch The Record's canonical / hreflang / sitemap URLs between phases.

  python3 tools/set_phase.py github
      Point everything at the GitHub Pages URLs. Always allowed.

  python3 tools/set_phase.py official --approved-by NAME --date YYYY-MM-DD
      Point the GitHub copies at the confirmed official wearemucho.com pages.
      Refuses unless: all official URLs and the official sitemap are filled in
      tools/record-urls.json, confirmed_by/confirmed_on are set, and
      `tools/record_check.py --official-preflight` reports 0 FAIL and 0 UNKNOWN.
      In the official phase the GitHub sitemap.xml is deleted (retired), so the
      GitHub copy never lists URLs that canonicalise elsewhere.

Only the marked head block, the WebPage "url", the EN "inDefinedTermSet" and
sitemap.xml are rewritten. Nothing is committed; review with `git diff`.
Undo: `python3 tools/set_phase.py github` or `git revert` the commit.
"""
import argparse, json, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "tools" / "record-urls.json"
START = "<!-- record-urls:start (managed by tools/set_phase.py; phase: {phase}) -->"
BLOCK_RE = re.compile(r"<!-- record-urls:start[^>]*-->.*?<!-- record-urls:end -->", re.S)


def url_map(cfg, phase):
    src = cfg["github"] if phase == "github" else cfg["official"]
    return {lang: src[lang] for lang in cfg["pages"]}


def head_block(cfg, phase, lang):
    urls = url_map(cfg, phase)
    lines = [START.format(phase=phase), f'<link rel="canonical" href="{urls[lang]}">']
    for l in cfg["pages"]:
        lines.append(f'<link rel="alternate" hreflang="{l}" href="{urls[l]}">')
    lines.append(f'<link rel="alternate" hreflang="x-default" href="{urls[cfg["x_default"]]}">')
    lines.append("<!-- record-urls:end -->")
    return "\n".join(lines)


def rewrite_page(cfg, phase, lang):
    path = ROOT / cfg["pages"][lang]["file"]
    s = path.read_text(encoding="utf-8")
    urls = url_map(cfg, phase)
    if len(BLOCK_RE.findall(s)) != 1:
        sys.exit(f"STOP: {path.name}: expected exactly one record-urls block")
    s = BLOCK_RE.sub(lambda m: head_block(cfg, phase, lang), s)
    # WebPage url: the "url" line directly after the WebPage name line
    s, n = re.subn(r'("@type": "WebPage",\n "name": "[^"]+",\n "url": ")[^"]+(")',
                   lambda m: m.group(1) + urls[lang] + m.group(2), s)
    if n != 1:
        sys.exit(f"STOP: {path} — WebPage url not found exactly once ({n})")
    if lang == cfg["x_default"]:
        s, n = re.subn(r'("inDefinedTermSet": ")[^"]+(")',
                       lambda m: m.group(1) + urls[lang] + "#method" + m.group(2), s)
        if n > 1:
            sys.exit(f"STOP: {path} — inDefinedTermSet found {n} times")
    path.write_text(s, encoding="utf-8")


def write_sitemap(cfg, phase):
    sm = ROOT / "sitemap.xml"
    if phase == "official":
        if sm.exists():
            sm.unlink()
        return
    rows = []
    for lang, p in cfg["pages"].items():
        rows.append(f"  <url>\n    <loc>{cfg['github'][lang]}</loc>\n    <lastmod>{p['lastmod']}</lastmod>\n  </url>")
    sm.write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
                  '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                  + "\n".join(rows) + "\n</urlset>\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["github", "official"])
    ap.add_argument("--approved-by")
    ap.add_argument("--date")
    a = ap.parse_args()
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))

    if a.phase == "official":
        off = cfg["official"]
        missing = [k for k in list(cfg["pages"]) + ["sitemap", "confirmed_by", "confirmed_on"] if not off.get(k)]
        if missing:
            sys.exit(f"REFUSED: official phase needs confirmed values in tools/record-urls.json: {missing}")
        if not (a.approved_by and a.date):
            sys.exit("REFUSED: pass --approved-by NAME --date YYYY-MM-DD")
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "record_check.py"), "--official-preflight"], cwd=ROOT)
        if r.returncode != 0:
            sys.exit("REFUSED: official preflight did not pass (needs 0 FAIL and 0 UNKNOWN)")
        cfg["approval"] = {"by": a.approved_by, "date": a.date}
    else:
        cfg["approval"] = None

    for lang in cfg["pages"]:
        rewrite_page(cfg, a.phase, lang)
    write_sitemap(cfg, a.phase)
    cfg["phase"] = a.phase
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Phase set to {a.phase}. Nothing committed — review with `git diff`, then run tools/record_check.py.")


if __name__ == "__main__":
    main()
