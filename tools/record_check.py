#!/usr/bin/env python3
"""Checks for Mucho — The Record. Read-only; Python standard library only.

  python3 tools/record_check.py                    # technical + content groups (local files)
  python3 tools/record_check.py --group technical  # gate a technical change
  python3 tools/record_check.py --group content    # gate a factual change
  python3 tools/record_check.py --compare-visible REF   # visible text must equal git REF
  python3 tools/record_check.py --live [--expect-deployed] [--ua-diagnostic]
  python3 tools/record_check.py --official-preflight

Statuses: PASS · FAIL (genuine problem) · WARN (optional / unverified) ·
UNKNOWN (a network check could not run). The exit code is 1 if any selected
group has a FAIL (and, for --official-preflight, any UNKNOWN).

"structure" checks are a sanity check (doctype, charset, tag balance, IDs,
one h1/title). They are NOT full HTML validation; use the W3C Nu checker
(https://validator.w3.org/nu/) on the live URLs for that.
Simulated crawler user-agents (--ua-diagnostic) are diagnostics only and are
never evidence that a real crawler can access, or has accessed, the site.
"""
import argparse, hashlib, html, json, re, subprocess, sys
import urllib.error, urllib.request, urllib.robotparser
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "tools" / "record-urls.json").read_text(encoding="utf-8"))
VERIFY_FILE = "google21b31cba6da2b201.html"
VERIFY_SHA = "9d67a3fc"  # first 8 hex of the file's sha256 at commit 26722e9
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
BLOCKING = {"noindex", "none", "nofollow"}
# Exact strings only: approved spellings and names that must not appear.
APPROVED = {"Peter Nicks": ["Pete Nicks"]}
# A1 (facts-first layout): section order on the page and in the Contents rail
SECTION_ORDER = ["facts", "not-clients", "fit", "method", "awards", "people",
                 "narrative", "verify", "log", "index", "cases"]
A1_DONE = {"index.html", "es/index.html", "ca/index.html"}  # files on the A1 layout
FIRST_FACT_MAX = 25000    # bytes before id="facts"
# A2/A3/A5 (Nov 2026 content): files already updated; add es/ca when they are done
CONTENT_DONE = {"index.html", "es/index.html", "ca/index.html"}
PLACEHOLDERS = ["[date]", "[fecha]", "[data]"]
OFFICES = {"en": ("Mucho has two offices", "does not have offices in New York, Paris or Melbourne"),
           "es": ("Mucho tiene dos oficinas", "no tiene oficinas en Nueva York, París ni Melbourne"),
           "ca": ("Mucho té dues oficines", "no té oficines a Nova York, París ni Melbourne")}
APPROVED_A3 = {"Principal": ["Pricipal"], "Toulouse": ["Toulousse"], "DocMorris": ["Doc Morris"],
               "development": ["developement"], "led by": ["is lead by"], "Gran Laus": ["Grand Laus"]}
AI_TOOL_NAMES = ["ChatGPT", "Gemini", "Claude", "DeepSeek", "Perplexity"]
SEARCH_BOTS = ["Googlebot", "Bingbot", "OAI-SearchBot", "Claude-SearchBot", "PerplexityBot"]
UAS = {
    "Googlebot": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Bingbot": "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "OAI-SearchBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot",
    "GPTBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.1; +https://openai.com/gptbot",
    "Claude-SearchBot": "Mozilla/5.0 (compatible; Claude-SearchBot/1.0; +https://www.anthropic.com)",
    "ClaudeBot": "Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
    "PerplexityBot": "Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)",
}
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"

counts = {}


def report(group, status, msg):
    counts.setdefault(group, {}).setdefault(status, 0)
    counts[group][status] += 1
    print(f"[{status:7}] {group:9} {msg}")


# ---------------------------------------------------------------- parsing
class Page(HTMLParser):
    """Collects everything the checks need in one pass."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.tag_errors = [], []
        self.lang = None; self.metas = []; self.links = []; self.canon = []; self.alts = []
        self.ids = []; self.hrefs = []; self.h1 = 0; self.titles = 0; self.charset = False
        self.text = []; self.skip = 0; self.in_head = False; self.cur_h1 = None; self.h1_text = ""

    def handle_decl(self, d):
        self.doctype = d

    def handle_starttag(self, t, a):
        a = {k.lower(): (v or "") for k, v in a}
        if t not in VOID:
            self.stack.append((t, self.getpos()[0]))
        if t == "html": self.lang = a.get("lang")
        if t == "head": self.in_head = True
        if t == "meta":
            self.metas.append(a)
            if "charset" in a: self.charset = True
        if t == "link":
            rel = a.get("rel", "").lower().split()
            if "canonical" in rel: self.canon.append(a.get("href", ""))
            if "alternate" in rel and a.get("hreflang"): self.alts.append((a["hreflang"].lower(), a.get("href", "")))
        if "id" in a: self.ids.append(a["id"])
        if t == "a" and "href" in a: self.hrefs.append((a["href"], self.getpos()[0]))
        if t in ("img", "script", "source", "iframe") and a.get("src"): self.hrefs.append((a["src"], self.getpos()[0]))
        if t == "h1": self.h1 += 1; self.cur_h1 = []
        if t == "title": self.titles += 1
        if t in ("script", "style", "head", "template", "noscript"): self.skip += 1

    def handle_endtag(self, t):
        if t in VOID: return
        if t == "head": self.in_head = False
        if t in ("script", "style", "head", "template", "noscript"): self.skip = max(0, self.skip - 1)
        if t == "h1" and self.cur_h1 is not None:
            self.h1_text = " ".join("".join(self.cur_h1).split()); self.cur_h1 = None
        if self.stack and self.stack[-1][0] == t:
            self.stack.pop()
        else:
            self.tag_errors.append((t, self.getpos()[0]))
            for k in range(len(self.stack) - 1, -1, -1):
                if self.stack[k][0] == t:
                    del self.stack[k:]; break

    def handle_data(self, d):
        if self.cur_h1 is not None: self.cur_h1.append(d)
        if not self.skip and d.strip(): self.text.append(" ".join(d.split()))


def parse(s):
    p = Page(); p.doctype = None; p.feed(s); p.close()
    p.src = s
    p.jsonld = []
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', s, re.S):
        try: p.jsonld.append(json.loads(m.group(1)))
        except Exception as e: p.jsonld.append(e)
    return p


def visible_text(s):
    return "\n".join(parse(s).text)


def pages():
    out = {}
    for lang, info in CFG["pages"].items():
        f = ROOT / info["file"]
        out[lang] = (info["file"], parse(f.read_text(encoding="utf-8"))) if f.exists() else (info["file"], None)
    return out


def phase_urls():
    src = CFG["github"] if CFG["phase"] == "github" else CFG["official"]
    return {lang: src[lang] for lang in CFG["pages"]}


def blocking_robots_meta(metas):
    hits = []
    for m in metas:
        name = m.get("name", "").strip().lower()
        if name in ("robots",) or name.endswith("bot") or name in ("googlebot-news",):
            tokens = {t.strip() for t in m.get("content", "").lower().replace(";", ",").split(",")}
            if tokens & BLOCKING: hits.append(f'{name}="{m.get("content")}"')
    return hits


# ---------------------------------------------------------------- technical
def technical(P):
    G = "technical"
    urls = phase_urls()
    url_to_lang = {u: l for l, u in urls.items()}
    expected_alts = sorted([(l, urls[l]) for l in CFG["pages"]] + [("x-default", urls[CFG["x_default"]])])
    vf = ROOT / VERIFY_FILE
    if vf.exists() and hashlib.sha256(vf.read_bytes()).hexdigest().startswith(VERIFY_SHA):
        report(G, "PASS", f"verification file {VERIFY_FILE} present and unchanged")
    else:
        report(G, "FAIL", f"verification file {VERIFY_FILE} missing or changed")
    base = CFG["github"][CFG["x_default"]]
    for lang, (f, p) in P.items():
        if p is None:
            report(G, "FAIL", f"{f}: file missing"); continue
        # structure (sanity, not validation)
        prob = []
        if not p.doctype or p.doctype.lower() != "doctype html": prob.append("doctype")
        if not p.charset: prob.append("charset")
        if p.tag_errors or p.stack: prob.append(f"{len(p.tag_errors)} mismatched / {len(p.stack)} unclosed tags")
        dup = [i for i in set(p.ids) if p.ids.count(i) > 1]
        if dup: prob.append(f"duplicate ids {dup[:5]}")
        if p.h1 != 1: prob.append(f"{p.h1} <h1>")
        if p.titles != 1: prob.append(f"{p.titles} <title>")
        report(G, "FAIL" if prob else "PASS", f"{f}: structure sanity (not full validation)" + (f": {prob}" if prob else ""))
        # robots meta
        hits = blocking_robots_meta(p.metas)
        report(G, "FAIL" if hits else "PASS", f"{f}: no blocking robots meta" + (f": {hits}" if hits else ""))
        # html lang
        report(G, "PASS" if p.lang == lang else "FAIL", f"{f}: <html lang=\"{p.lang}\"> (expected {lang})")
        # canonical
        ok = p.canon == [urls[lang]] and urls[lang].startswith("https://")
        report(G, "PASS" if ok else "FAIL", f"{f}: canonical {p.canon} (expected [{urls[lang]}])")
        # hreflang
        got = sorted(p.alts)
        report(G, "PASS" if got == expected_alts else "FAIL",
               f"{f}: hreflang set complete, reciprocal, x-default={CFG['x_default']}" + ("" if got == expected_alts else f"; found {got}"))
        # JSON-LD
        bad = [str(d) for d in p.jsonld if isinstance(d, Exception)]
        report(G, "FAIL" if bad else "PASS", f"{f}: {len(p.jsonld)} JSON-LD blocks parse" + (f": {bad}" if bad else ""))
        docs = [d for d in p.jsonld if isinstance(d, dict)]
        wp = [d for d in docs if d.get("@type") == "WebPage"]
        if len(wp) != 1:
            report(G, "FAIL", f"{f}: expected one WebPage block, found {len(wp)}")
        else:
            report(G, "PASS" if wp[0].get("url") == urls[lang] else "FAIL", f"{f}: WebPage.url = canonical ({wp[0].get('url')})")
            report(G, "PASS" if wp[0].get("inLanguage") == lang else "WARN", f"{f}: WebPage.inLanguage = {wp[0].get('inLanguage')}")
        for d in docs:
            if "inDefinedTermSet" in d:
                v = d["inDefinedTermSet"]
                report(G, "PASS" if v.startswith(urls[CFG['x_default']]) else "FAIL", f"{f}: inDefinedTermSet on current phase URL ({v})")
        # links
        page_url = CFG["github"][lang]
        idset = {html.unescape(i) for i in p.ids}
        placeholders, external, broken = 0, set(), []
        for href, line in p.hrefs:
            h = href.strip()
            if h == "#":
                placeholders += 1; continue
            if h.startswith("#"):
                if html.unescape(h[1:]) not in idset: broken.append((line, h))
                continue
            if h.startswith(("mailto:", "tel:")): continue
            absu = urljoin(page_url, h)
            sp = urlsplit(absu)
            if sp.netloc in ("github.com", "www.github.com") and "/muchoai/" in sp.path:
                broken.append((line, h + " (points to the repo, not the site)")); continue
            if absu.startswith(base):
                rel = sp.path[len(urlsplit(base).path):]
                target = ROOT / (rel + "index.html" if rel == "" or rel.endswith("/") else rel)
                if not target.exists(): broken.append((line, h))
                elif sp.fragment and rel.endswith("/") and target.name == "index.html":
                    tp = parse(target.read_text(encoding="utf-8"))
                    if html.unescape(sp.fragment) not in {html.unescape(i) for i in tp.ids}: broken.append((line, h))
                continue
            if sp.scheme in ("http", "https"):
                external.add(sp.netloc)
        report(G, "FAIL" if broken else "PASS", f"{f}: internal links resolve" + (f"; broken: {broken[:8]}" if broken else ""))
        if placeholders:
            report(G, "WARN", f"{f}: {placeholders} placeholder links (href=\"#\")")
        # language switcher reaches each sibling
        sib = {l: CFG["github"][l] for l in CFG["pages"] if l != lang}
        reached = {urljoin(page_url, h) for h, _ in p.hrefs}
        missing = [l for l, u in sib.items() if u not in reached]
        report(G, "FAIL" if missing else "PASS", f"{f}: language links reach {sorted(sib)}" + (f"; missing {missing}" if missing else ""))
        report(G, "PASS", f"{f}: external hosts linked (not fetched): {len(external)}")
    # sitemap vs canonicals
    sm = ROOT / "sitemap.xml"
    canon = {p.canon[0]: l for l, (_, p) in P.items() if p and len(p.canon) == 1}
    if CFG["phase"] == "official":
        report(G, "PASS" if not sm.exists() else "FAIL", "sitemap.xml retired in official phase")
    elif not sm.exists():
        report(G, "FAIL", "sitemap.xml missing")
    else:
        try:
            t = ET.parse(sm)
            ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
            if t.getroot().tag != ns + "urlset": raise ValueError("root is not a sitemaps.org urlset")
            entries = [(u.findtext(ns + "loc", "").strip(), (u.findtext(ns + "lastmod") or "").strip()) for u in t.getroot()]
            locs = [e[0] for e in entries]
            report(G, "PASS", "sitemap.xml well-formed, sitemaps.org urlset")
            report(G, "PASS" if len(locs) == len(set(locs)) else "FAIL", "sitemap: no duplicate <loc>")
            not_canon = [l for l in locs if l not in canon]
            report(G, "FAIL" if not_canon else "PASS", "sitemap: every <loc> is a page's own canonical" + (f"; not canonical: {not_canon}" if not_canon else ""))
            missing = [u for u in canon if u not in locs]
            report(G, "FAIL" if missing else "PASS", "sitemap: every canonical is listed" + (f"; missing: {missing}" if missing else ""))
            from datetime import date
            bad = [m for _, m in entries if m and (not re.fullmatch(r"\d{4}-\d{2}-\d{2}", m) or m > date.today().isoformat())]
            report(G, "FAIL" if bad else "PASS", "sitemap: lastmod values valid and not in the future" + (f": {bad}" if bad else ""))
        except Exception as e:
            report(G, "FAIL", f"sitemap.xml: {e}")
    rb = (ROOT / "robots.txt")
    if rb.exists():
        rp = urllib.robotparser.RobotFileParser(); rp.parse(rb.read_text().splitlines())
        blocked = [b for b in SEARCH_BOTS if not rp.can_fetch(b, "/")]
        report(G, "FAIL" if blocked else "PASS", "project robots.txt is non-blocking (has no effect at this path; checked for custom-domain safety)" + (f"; blocks {blocked}" if blocked else ""))


# ---------------------------------------------------------------- content guards
def org_desc(p):
    for d in p.jsonld:
        if isinstance(d, dict) and d.get("@type") == "Organization": return d.get("description", "")
    return ""


def never_names(desc):
    m = re.search(r"(?:never worked for|nunca ha trabajado para|no ha treballat mai per a) (.+?)\.$", desc)
    if not m: return None
    return [x.strip() for x in re.split(r",\s*|\s+(?:or|ni|o)\s+", m.group(1)) if x.strip()]


def content(P):
    G = "content"
    lists = {}
    for lang, (f, p) in P.items():
        if p is None: continue
        nc = re.search(r'<p class="nc-names">(.*?)</p>', p.src, re.S)
        visible_nc = [html.unescape(x).replace("\xa0", " ").strip() for x in re.split(r"·", html.unescape(nc.group(1)))] if nc else []
        visible_nc = [x for x in visible_nc if x]
        cl = re.search(r'<tr><th>(?:Clients|Clientes)</th><td class="val">(.*?)</td>', p.src, re.S)
        client_row = html.unescape(re.sub(r"<[^>]+>", "", cl.group(1))) if cl else ""
        never = never_names(org_desc(p))
        lists[lang] = (tuple(sorted(never or [])), tuple(sorted(visible_nc)), client_row)
        if never is None:
            report(G, "FAIL", f"{f}: JSON-LD 'never worked for' sentence not found"); continue
        miss = [n for n in visible_nc if n not in never]
        extra = [n for n in never if n not in visible_nc]
        report(G, "FAIL" if (miss or extra) else "PASS", f"{f}: JSON-LD 'never' list = visible not-clients list"
               + (f"; missing {miss}" if miss else "") + (f"; extra {extra}" if extra else ""))
        clash = [n for n in never if re.search(r"\b" + re.escape(n) + r"\b", client_row)]
        report(G, "FAIL" if clash else "PASS", f"{f}: no name is both a listed client and 'never worked for'" + (f"; {clash}" if clash else ""))
        for good, bads in APPROVED.items():
            found = {b: p.src.count(b) for b in bads if b in p.src}
            report(G, "FAIL" if found else "PASS", f"{f}: approved spelling '{good}'" + (f"; found {found}" if found else ""))
        arts = p.src.count("<article")
        h2 = re.search(r'<section class="sec" id="cases">\s*<h2>([^<]*)</h2>', p.src)
        num = re.search(r"\d+", h2.group(1)) if h2 else None
        jump = re.search(r'<ol class="industry-jump">(.*?)</ol>', p.src, re.S)
        by_ind = sum(map(int, re.findall(r"<span>(\d+)</span>", jump.group(1)))) if jump else None
        ok = num is not None and int(num.group()) == arts == by_ind
        report(G, "PASS" if ok else "FAIL", f"{f}: case counts agree (heading {num.group() if num else None}, industries {by_ind}, articles {arts})")
        bad = "FAIL" if f in A1_DONE else "WARN"
        pend = "" if f in A1_DONE else " (A1 pending)"
        wp = [d for d in p.jsonld if isinstance(d, dict) and d.get("@type") == "WebPage"]
        report(G, bad if wp and "mainEntity" in wp[0] else "PASS", f"{f}: no hidden case list in WebPage JSON-LD{pend}")
        secs = re.findall(r'<section class="sec" id="([^"]+)"', p.src)
        nav = re.search(r'<nav class="rail".*?</nav>', p.src, re.S)
        rail = re.findall(r'href="#([^"]+)"', nav.group()) if nav else []
        report(G, "PASS" if secs == SECTION_ORDER else bad, f"{f}: section order{pend}" + ("" if secs == SECTION_ORDER else f"; found {secs}"))
        report(G, "PASS" if rail == secs else "FAIL", f"{f}: Contents rail follows section order")
        bad2 = "FAIL" if f in CONTENT_DONE else "WARN"
        pend2 = "" if f in CONTENT_DONE else " (Nov content pending)"
        for good, bads in APPROVED_A3.items():
            found = {b: p.src.count(b) for b in bads if b in p.src}
            report(G, bad2 if found else "PASS", f"{f}: approved spelling '{good}'{pend2}" + (f"; found {found}" if found else ""))
        vis = " ".join(" ".join(p.text).split())
        ai = [n for n in AI_TOOL_NAMES if re.search(r"\b" + n + r"\b", vis)]
        report(G, bad2 if ai else "PASS", f"{f}: no AI tool names in visible text{pend2}" + (f"; found {ai}" if ai else ""))
        ph_found = [x for x in PLACEHOLDERS if x in vis]
        report(G, bad2 if ph_found else "PASS", f"{f}: no [date] placeholders{pend2}" + (f"; found {ph_found}" if ph_found else ""))
        ph = p.src.count('href="#"')
        report(G, bad2 if ph else "PASS", f"{f}: no placeholder links{pend2}" + (f"; {ph} left" if ph else ""))
        if f in CONTENT_DONE:
            laus = "1 Gran Laus, 12 Laus" in vis
            report(G, "PASS" if laus else "FAIL", f"{f}: awards read '1 Gran Laus, 12 Laus' (M10)")
            offices = all(x in vis for x in OFFICES.get(lang, OFFICES["en"]))
            report(G, "PASS" if offices else "FAIL", f"{f}: Offices row states two offices and none in New York, Paris or Melbourne")
            m = re.search(r"(?:Clients include|Entre sus clientes están|Entre els seus clients hi ha) (.+?)\. ", org_desc(p))
            named = [x.strip() for x in re.split(r",\s*|\s+(?:and|y|i)\s+", m.group(1))] if m else []
            missing = [n for n in named if n not in client_row]
            report(G, "PASS" if named and not missing else "FAIL", f"{f}: every client in the JSON-LD description is in the visible Clients row"
                   + (f"; missing {missing}" if missing else ""))
        for sec in ("awards", "talks", "media"):
            ok = p.src.count(f"<!-- list:{sec}:start -->") == 1 and p.src.count(f"<!-- list:{sec}:end -->") == 1
            report(G, "PASS" if ok else "FAIL", f"{f}: {sec} list markers present once (tools/build_lists.py)")
        off = len(p.src[:p.src.find('id="facts"')].encode())
        report(G, "PASS" if 0 < off <= FIRST_FACT_MAX else bad, f"{f}: first fact at byte {off:,} (target ≤ {FIRST_FACT_MAX:,}){pend}")
    aw_links = {f: tuple(re.findall(r'href="([^"]+)"', re.search(r'<section class="sec" id="awards">.*?</section>', p.src, re.S).group()))
                for lang, (f, p) in P.items() if p is not None and '<section class="sec" id="awards">' in p.src}
    if len(aw_links) > 1:
        same = len(set(aw_links.values())) == 1
        report(G, "PASS" if same else "FAIL", "awards links identical across languages")
    if len(lists) > 1:
        vals = list(lists.values())
        report(G, "PASS" if all(v[0] == vals[0][0] for v in vals) else "FAIL", "JSON-LD 'never' lists identical across languages")
        report(G, "PASS" if all(v[1] == vals[0][1] for v in vals) else "FAIL", "visible not-clients lists identical across languages")


# ---------------------------------------------------------------- visible-text comparison
def compare_visible(P, ref):
    G = "visible"
    for lang, (f, p) in P.items():
        try:
            old = subprocess.run(["git", "show", f"{ref}:{f}"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        except subprocess.CalledProcessError:
            report(G, "UNKNOWN", f"{f}: not in {ref}"); continue
        a, b = visible_text(old).splitlines(), p and "\n".join(p.text).splitlines()
        if a == b:
            report(G, "PASS", f"{f}: visible text identical to {ref}")
        else:
            import difflib
            d = [l for l in difflib.unified_diff(a, b, lineterm="", n=0) if l[:1] in "+-" and l[:3] not in ("+++", "---")]
            report(G, "FAIL", f"{f}: visible text differs from {ref} ({len(d)} lines)")
            for l in d[:20]: print("            " + l[:160])


# ---------------------------------------------------------------- network
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None


OPENER = urllib.request.build_opener(NoRedirect)


def fetch(url, ua=BROWSER_UA):
    try:
        with OPENER.open(urllib.request.Request(url, headers={"User-Agent": ua}), timeout=20) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, b""
    except Exception as e:
        return None, {}, str(e).encode()


def live(P, expect_deployed, ua_diag):
    G = "live"
    for lang, (f, p) in P.items():
        url = CFG["github"][lang]
        st, h, body = fetch(url)
        if st is None:
            report(G, "UNKNOWN", f"{url}: network unavailable ({body.decode()[:80]})"); continue
        xr = h.get("x-robots-tag", "")
        blocking = any(t in xr.lower() for t in BLOCKING)
        report(G, "PASS" if st == 200 and not blocking else "FAIL",
               f"{url}: HTTP {st}" + (f" → {h.get('location')}" if 300 <= st < 400 else "") + (f"; X-Robots-Tag: {xr}" if xr else ""))
        if st == 200:
            same = hashlib.sha256(body).hexdigest() == hashlib.sha256((ROOT / f).read_bytes()).hexdigest()
            report(G, "PASS" if same else ("FAIL" if expect_deployed else "WARN"), f"{url}: live bytes {'match' if same else 'differ from'} local {f}")
        if ua_diag:
            for name, ua in UAS.items():
                s2, h2, _ = fetch(url, ua)
                print(f"[DIAG   ] live      {url} as '{name}' UA string: HTTP {s2} — diagnostic only, not evidence of real crawler access")
    for name, url in [("sitemap", CFG["github"]["en"] + "sitemap.xml"),
                      ("verification", CFG["github"]["en"] + VERIFY_FILE)]:
        st, _, body = fetch(url)
        if st is None: report(G, "UNKNOWN", f"{url}: network unavailable"); continue
        same = st == 200 and body == (ROOT / url.rsplit("/", 1)[1]).read_bytes()
        report(G, "PASS" if same else ("FAIL" if expect_deployed or name == "verification" else "WARN"), f"{url}: HTTP {st}{', matches local' if same else ''}")
    root = "https://muchoai.github.io/robots.txt"
    st, _, body = fetch(root)
    if st is None:
        report(G, "UNKNOWN", f"{root}: network unavailable")
    elif st == 404:
        report(G, "PASS", f"{root}: 404 (no host-level rules: crawling allowed)")
    elif st == 200:
        rp = urllib.robotparser.RobotFileParser(); rp.parse(body.decode("utf-8", "replace").splitlines())
        path = urlsplit(CFG["github"]["en"]).path
        blocked = [b for b in SEARCH_BOTS + ["GPTBot", "ClaudeBot"] if not rp.can_fetch(b, path)]
        report(G, "FAIL" if blocked else "PASS", f"{root}: host-level rules" + (f" block {blocked}" if blocked else " allow all checked bots"))
    else:
        report(G, "WARN", f"{root}: HTTP {st}")


def official_preflight(P):
    G = "preflight"
    off = CFG["official"]
    langs = list(CFG["pages"])
    missing = [k for k in langs + ["sitemap", "confirmed_by", "confirmed_on"] if not off.get(k)]
    if missing:
        report(G, "FAIL", f"official values not confirmed in tools/record-urls.json: {missing}"); return
    omap = {l: off[l] for l in langs}
    exp_alts = sorted([(l, omap[l]) for l in langs] + [("x-default", omap[CFG["x_default"]])])
    for lang in langs:
        url = omap[lang]
        st, h, body = fetch(url)
        if st is None: report(G, "UNKNOWN", f"{url}: network unavailable"); continue
        if st != 200:
            report(G, "FAIL", f"{url}: HTTP {st}" + (f" → {h.get('location')} (redirects are not accepted)" if 300 <= st < 400 else "")); continue
        rp = parse(body.decode("utf-8", "replace"))
        loc = P[lang][1]
        report(G, "PASS" if rp.lang == lang else "FAIL", f"{url}: <html lang=\"{rp.lang}\"> (expected {lang})")
        report(G, "PASS" if rp.canon == [url] else "FAIL", f"{url}: self-referencing canonical ({rp.canon})")
        report(G, "PASS" if sorted(rp.alts) == exp_alts else "FAIL", f"{url}: reciprocal official hreflang")
        hits = blocking_robots_meta(rp.metas); xr = h.get("x-robots-tag", "")
        report(G, "FAIL" if hits or any(t in xr.lower() for t in BLOCKING) else "PASS", f"{url}: no noindex (meta {hits}, header '{xr}')")
        report(G, "PASS" if rp.h1_text == loc.h1_text else "FAIL", f"{url}: same <h1> as GitHub {lang} page")
        ids_l = {i for i in loc.ids}; ids_r = {i for i in rp.ids}
        report(G, "PASS" if ids_l == ids_r else "FAIL", f"{url}: same case/section IDs as GitHub {lang} page ({len(ids_r)} vs {len(ids_l)})")
        a, b = set(loc.text), set(rp.text)
        share = len(a & b) / max(1, len(a))
        report(G, "PASS" if share >= 0.98 else "FAIL", f"{url}: {share:.1%} of GitHub {lang} text blocks present (needs ≥98%)")
        if share < 1:
            for t in sorted(a - b)[:10]: print("            missing: " + t[:150])
        n1, n2 = never_names(org_desc(rp)), never_names(org_desc(loc))
        report(G, "PASS" if n1 == n2 else "FAIL", f"{url}: JSON-LD 'never' list identical to GitHub {lang} page")
    st, _, body = fetch(off["robots"])
    if st is None: report(G, "UNKNOWN", f"{off['robots']}: network unavailable")
    elif st == 200:
        rpar = urllib.robotparser.RobotFileParser(); rpar.parse(body.decode("utf-8", "replace").splitlines())
        blocked = [(b, l) for b in SEARCH_BOTS for l in langs if not rpar.can_fetch(b, urlsplit(omap[l]).path)]
        report(G, "FAIL" if blocked else "PASS", f"{off['robots']}: search bots may fetch the official pages" + (f"; blocked {blocked}" if blocked else ""))
    else:
        report(G, "PASS" if st == 404 else "FAIL", f"{off['robots']}: HTTP {st}")
    locs, queue, seen = set(), [off["sitemap"]], 0
    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    while queue and seen < 30:
        u = queue.pop(0); seen += 1
        st, _, body = fetch(u)
        if st is None: report(G, "UNKNOWN", f"{u}: network unavailable"); return
        if st != 200: report(G, "FAIL", f"{u}: HTTP {st}"); return
        try: r = ET.fromstring(body)
        except ET.ParseError as e: report(G, "FAIL", f"{u}: invalid XML ({e})"); return
        for el in r:
            loc = (el.findtext(ns + "loc") or "").strip()
            if r.tag == ns + "sitemapindex":
                queue.append(loc)
            else:
                locs.add(loc)
    miss = [u for u in omap.values() if u not in locs]
    report(G, "FAIL" if miss else "PASS", "official sitemap lists every confirmed official page" + (f"; missing {miss}" if miss else ""))
    gh = [u for u in locs if "muchoai.github.io" in u]
    report(G, "FAIL" if gh else "PASS", "official sitemap lists no GitHub URLs")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--group", choices=["technical", "content", "all"], default="all")
    ap.add_argument("--compare-visible", metavar="GIT_REF")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--expect-deployed", action="store_true")
    ap.add_argument("--ua-diagnostic", action="store_true")
    ap.add_argument("--official-preflight", action="store_true")
    a = ap.parse_args()
    P = pages()
    print(f"Phase: {CFG['phase']}")
    gating = set()
    if a.official_preflight:
        official_preflight(P); gating = {"preflight"}
    else:
        if a.group in ("technical", "all"): technical(P); gating.add("technical")
        if a.group in ("content", "all"): content(P); gating.add("content")
        if a.compare_visible: compare_visible(P, a.compare_visible); gating.add("visible")
        if a.live: live(P, a.expect_deployed, a.ua_diagnostic); gating.add("live")
    print("\nSummary:")
    fail = False
    for g, c in counts.items():
        print(f"  {g:9} " + "  ".join(f"{k} {v}" for k, v in sorted(c.items())))
        if g in gating and (c.get("FAIL") or (g == "preflight" and c.get("UNKNOWN"))): fail = True
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
