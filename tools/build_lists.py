#!/usr/bin/env python3
"""Rebuild the Awards, Talks and Media lists on The Record from the sheet.

The sheet "Ai visibility sheet" (Mucho Google account) has a Public tab with:
    Section, Text EN, Text ES, Text CA, Link 1, Link 2
filled automatically with the ticked rows of the Awards, Talks and Media tabs.

This script:
  1. reads that CSV (a file, or the published URL in tools/record-lists.json);
  2. checks every row (see validate()); any problem stops it with no change;
  3. rewrites the three lists between the markers in index.html, es/ and ca/:
        <!-- list:awards:start --> ... <!-- list:awards:end -->
  4. if anything changed: adds a dated line to each corrections log, updates the
     "Last updated" stamp, the WebPage dateModified, the Organization "award"
     list (EN texts of the Awards rows) and the sitemap lastmod.

It never touches anything outside those places. Run tools/record_check.py after.

Usage:
  python3 tools/build_lists.py --csv lists.csv [--today 2026-11-03] [--dry-run]
  python3 tools/build_lists.py --url          (fetch the published CSV)
Exit code 0 on success (changed or not), 1 on any validation problem.
If GITHUB_OUTPUT is set, writes changed=true|false to it.
"""
import argparse, csv, datetime, html, io, json, os, re, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "tools" / "record-lists.json").read_text(encoding="utf-8"))
PAGES = {"en": "index.html", "es": "es/index.html", "ca": "ca/index.html"}
SECTIONS = ["Awards", "Talks", "Media"]
SEP = {"Awards": ", ", "Talks": "; ", "Media": ", "}
HEADER = ["Section", "Text EN", "Text ES", "Text CA", "Link 1", "Link 2"]
YEAR = re.compile(r"\b(19|20)\d{2}\b")
MARK = lambda s, end: f"<!-- list:{s.lower()}:{'end' if end else 'start'} -->"

MONTHS = {
    "en": "January February March April May June July August September October November December".split(),
    "es": "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split(),
    "ca": "gener febrer març abril maig juny juliol agost setembre octubre novembre desembre".split(),
}
def long_date(lang, d):
    m = MONTHS[lang][d.month - 1]
    if lang == "en": return f"{d.day} {m} {d.year}"
    if lang == "es": return f"{d.day} de {m} de {d.year}"
    return f"{d.day} {'d' + chr(39) if m[0] in 'aeiou' else 'de '}{m} de {d.year}"

LOG = {
    "en": {"add": "Added to the {s} list: {n}.", "remove": "Removed from the {s} list: {n}.", "links": "Source links updated: {n}.",
           "text": "Wording updated: {n}.", "s": {"Awards": "awards", "Talks": "talks", "Media": "media appearances"}},
    "es": {"add": "Añadido a la lista de {s}: {n}.", "remove": "Retirado de la lista de {s}: {n}.", "links": "Enlaces de origen actualizados: {n}.",
           "text": "Texto actualizado: {n}.", "s": {"Awards": "premios", "Talks": "charlas", "Media": "apariciones en medios"}},
    "ca": {"add": "Afegit a la llista {s}: {n}.", "remove": "Retirat de la llista {s}: {n}.", "links": "Enllaços d'origen actualitzats: {n}.",
           "text": "Text actualitzat: {n}.", "s": {"Awards": "de premis", "Talks": "de conferències", "Media": "d'aparicions als mitjans"}},
}
STAMP = {"en": r"(Last updated <b><time datetime=\")[^\"]+(\">)[^<]+(</time>)",
         "es": r"(Última actualización: <b><time datetime=\")[^\"]+(\">)[^<]+(</time>)",
         "ca": r"(Darrera actualització: <b><time datetime=\")[^\"]+(\">)[^<]+(</time>)"}


def fail(msgs):
    print("STOPPED, nothing changed. Fix these rows in the sheet, then publish again:")
    for m in msgs: print("  - " + m)
    sys.exit(1)


def read_rows(text):
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows or [c.strip() for c in rows[0][:6]] != HEADER:
        fail([f"the Public tab's first row should be {', '.join(HEADER)}"])
    out = []
    for n, r in enumerate(rows[1:], start=2):
        r = [c.strip() for c in (r + [""] * 6)[:6]]
        if not any(r): continue
        out.append({"row": n, "section": r[0], "en": r[1], "es": r[2], "ca": r[3], "l1": r[4], "l2": r[5]})
    return out


def validate(rows):
    errs, seen = [], set()
    for r in rows:
        where = f"Public row {r['row']} ({r['section'] or '?'}: {r['en'] or '?'})"
        if r["section"] not in SECTIONS:
            errs.append(f"{where}: unknown section '{r['section']}'. If it says #N/A, no row is ticked Ready")
        if not r["en"]:
            errs.append(f"{where}: Text on the page (EN) is empty")
        for k in ("en", "es", "ca"):
            if YEAR.search(r[k]): errs.append(f"{where}: the text contains a year; the page shows no years")
            if "<" in r[k] or ">" in r[k]: errs.append(f"{where}: the text contains < or >")
            if len(r[k]) > 160: errs.append(f"{where}: the text is longer than 160 characters")
        for k in ("l1", "l2"):
            if r[k] and not re.match(r"^https://[^\s\"<>]+$", r[k]):
                errs.append(f"{where}: Link {k[1]} must be a full https:// address with no spaces")
        if r["l2"] and not r["l1"]:
            errs.append(f"{where}: Link 2 is filled but Link 1 is empty")
        key = (r["section"], r["en"].lower())
        if key in seen: errs.append(f"{where}: this item appears twice")
        seen.add(key)
    for s in SECTIONS:
        if not any(r["section"] == s for r in rows):
            errs.append(f"the {s} list would be empty; at least one {s} row must be ticked Ready")
    if errs: fail(errs)


def esc(t): return html.escape(t, quote=False)
def a(url, label): return f'<a href="{html.escape(url, quote=True)}">{label}</a>'

def render_item(r, lang):
    text = r[lang] or r["en"]
    if r["l1"] and r["l2"]:
        return esc(text) + "<sup>" + a(r["l1"], "1") + " " + a(r["l2"], "2") + "</sup>"
    if r["l1"]:
        if ":" in text:  # "OFFF Festival: Barcelona, London…" links only the name before the colon
            head, tail = text.split(":", 1)
            return a(r["l1"], esc(head)) + ":" + esc(tail)
        return a(r["l1"], esc(text))
    return esc(text)


def current_items(src, section):
    """Items currently between the markers (EN page), as {text: (l1, l2)}."""
    m = re.search(re.escape(MARK(section, False)) + "(.*?)" + re.escape(MARK(section, True)), src, re.S)
    if not m: return None
    body, out = m.group(1), {}
    for part in re.split(re.escape(SEP[section]) + r"(?![^<]*</a>)(?![^<]*</sup>)", body):
        links = re.findall(r'href="([^"]+)"', part)
        part = re.sub(r"<!--.*?-->", "", part)
        text = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<sup>.*?</sup>", "", part))).strip()
        if text: out[text] = (html.unescape(links[0]) if links else "", html.unescape(links[1]) if len(links) > 1 else "")
    return out


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--csv"); g.add_argument("--url", action="store_true")
    ap.add_argument("--today"); ap.add_argument("--dry-run", action="store_true")
    o = ap.parse_args()
    if o.url:
        req = urllib.request.Request(CFG["csv_url"], headers={"User-Agent": "the-record-lists"})
        text = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    else:
        text = Path(o.csv).read_text(encoding="utf-8")
    rows = read_rows(text)
    validate(rows)
    today = datetime.date.fromisoformat(o.today) if o.today else \
        datetime.datetime.now(datetime.timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo("Europe/Madrid")).date()

    src = {lang: (ROOT / p).read_text(encoding="utf-8") for lang, p in PAGES.items()}
    for lang, s in src.items():
        for sec in SECTIONS:
            if s.count(MARK(sec, False)) != 1 or s.count(MARK(sec, True)) != 1:
                fail([f"{PAGES[lang]}: list markers for {sec} missing or repeated (page edited by hand?)"])

    # what changed (compared on the English page)
    changes = {"add": {}, "remove": {}, "links": [], "text": []}
    for sec in SECTIONS:
        old = current_items(src["en"], sec)
        new = {r["en"]: (r["l1"], r["l2"]) for r in rows if r["section"] == sec}
        add = [t for t in new if t not in old]; rem = [t for t in old if t not in new]
        if add: changes["add"][sec] = add
        if rem: changes["remove"][sec] = rem
        changes["links"] += [t for t in new if t in old and new[t] != old[t]]

    out = {}
    for lang, s in src.items():
        for sec in SECTIONS:
            items = SEP[sec].join(render_item(r, lang) for r in rows if r["section"] == sec)
            s = re.sub(re.escape(MARK(sec, False)) + ".*?" + re.escape(MARK(sec, True)),
                       lambda _: MARK(sec, False) + items + MARK(sec, True), s, count=1, flags=re.S)
        out[lang] = s
    # wording-only changes (ES/CA text edited, same EN text and links)
    if not any(changes.values()) and any(out[l] != src[l] for l in src):
        changes["text"] = ["ES/CA wording"]

    if all(out[l] == src[l] for l in src):
        print("No change: the page already matches the sheet.")
        gh_out("false"); return

    lines = {}
    for lang in PAGES:
        L, parts = LOG[lang], []
        for sec, names in changes["add"].items(): parts.append(L["add"].format(s=L["s"][sec], n=", ".join(names)))
        for sec, names in changes["remove"].items(): parts.append(L["remove"].format(s=L["s"][sec], n=", ".join(names)))
        if changes["links"]: parts.append(L["links"].format(n=", ".join(changes["links"])))
        if changes["text"] and not parts: parts.append(L["text"].format(n={"en": "translations", "es": "traducciones", "ca": "traduccions"}[lang]))
        lines[lang] = " ".join(parts)
        s = out[lang]
        s = s.replace('<div class="log">\n', '<div class="log">\n        <b>' + long_date(lang, today) + "</b> — " + esc(lines[lang]) + "<br>\n", 1)
        s, n = re.subn(STAMP[lang], lambda m: m.group(1) + today.isoformat() + m.group(2) + long_date(lang, today) + m.group(3), s, count=1)
        if n != 1: fail([f"{PAGES[lang]}: 'last updated' stamp not found"])
        s = re.sub(r'("dateModified": ")[^"]+(")', lambda m: m.group(1) + today.isoformat() + m.group(2), s, count=1)
        awards = json.dumps([r["en"] for r in rows if r["section"] == "Awards"], ensure_ascii=False)
        s = re.sub(r'"award": \[[^\]]*\]', lambda _: '"award": ' + awards.replace('", "', '","'), s, count=1)
        out[lang] = s

    sm = (ROOT / "sitemap.xml").read_text(encoding="utf-8")
    sm = re.sub(r"<lastmod>[^<]+</lastmod>", f"<lastmod>{today.isoformat()}</lastmod>", sm)

    print("Changes:", lines["en"])
    if o.dry_run:
        print("(dry run: nothing written)"); gh_out("false"); return
    for lang, p in PAGES.items(): (ROOT / p).write_text(out[lang], encoding="utf-8")
    (ROOT / "sitemap.xml").write_text(sm, encoding="utf-8")
    (ROOT / "lists-summary.txt").write_text(lines["en"] + "\n", encoding="utf-8")
    gh_out("true")


def gh_out(changed):
    p = os.environ.get("GITHUB_OUTPUT")
    if p:
        with open(p, "a") as f: f.write(f"changed={changed}\n")


if __name__ == "__main__":
    main()
