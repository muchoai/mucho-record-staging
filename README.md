# Mucho — The Record

Static HTML, served by GitHub Pages from `main` (root folder). No build step: what is committed is what is published, within about a minute of a push.

Live: https://muchoai.github.io/mucho-record-staging/ (EN) · `/es/` (ES) · `/ca/` (CA)

## Current state
- **Indexed by Google.** No `noindex` anywhere. Do not add one: it would remove the page from Google.
- **Canonical phase: `github`.** Each page's canonical points to its own GitHub URL; hreflang links the three languages to each other; `sitemap.xml` lists the three GitHub URLs.
- `google21b31cba6da2b201.html` is the Search Console verification file. **Never delete or rename it.**
- `robots.txt` here has no effect: crawlers only read `https://muchoai.github.io/robots.txt` (the host root). It is kept allow-all so it stays safe if this site is ever served from its own domain.

## Source of truth
This repo's `main` is the only master copy of the page. Start every editing session with `git pull`. HTML copies saved in chats, zips or project docs are reference only: copying one over `index.html` can silently undo earlier fixes (this happened in commit `1d7d811`).

## Before every push
```
python3 tools/record_check.py
```
- `technical` group: canonical, hreflang, sitemap, robots meta, links, structure. Must have 0 FAIL.
- `content` group: fact guards (not-clients list vs JSON-LD, client contradictions, approved spellings). Must have 0 FAIL for any content change.

After a push: `python3 tools/record_check.py --live --expect-deployed`.

## Canonical phases
URLs are managed by `tools/set_phase.py` and `tools/record-urls.json`. Do not edit the `record-urls` block in the page heads by hand.
- `python3 tools/set_phase.py github` — current phase.
- `official` — only once the official wearemucho.com pages exist with equivalent content in each language. It requires the confirmed official URLs in `tools/record-urls.json`, a passing `tools/record_check.py --official-preflight`, and `--approved-by NAME --date YYYY-MM-DD`. It then points each GitHub page's canonical at the matching same-language official page and retires `sitemap.xml`. The GitHub copy stays public: no redirect, no `noindex`.

## Rollback
- Undo one commit: `git revert <sha>` then `git push`.
- Restore the version indexed on 22 Oct 2026: `git checkout record-indexed-2026-10-22 -- .`, commit, push.

## Still needed for wearemucho.com/record (needs Craft CMS access)
Per `mucho-record-build-spec.docx`: footer link to `/record`, the page served at `/record` (Twig template), production `robots.txt` allowing the chosen search and AI crawlers, and `/record` in the SEOmatic sitemap.
