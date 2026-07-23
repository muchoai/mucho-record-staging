# Mucho — The Record (staging package)

This is a self-contained static site, ready to deploy anywhere, independent of Craft CMS. Purpose: get it live at a real URL now for testing, hand off to wearemucho.com when CMS access is available.

## What's in this folder
- `index.html` — the full record page (from `mucho-record-full.html`), with one addition: a `noindex, nofollow` meta tag so it doesn't get indexed at a temporary URL. **Remove that meta tag when this moves to `wearemucho.com/record`.**
- `robots.txt` — fully disallows crawling on this staging URL, as a second layer behind the noindex tag. **Replace with the production robots.txt in `mucho-record-build-spec.docx` (Task 3) at handoff.**

## Deploying this now (no code required)
Any static host works since there's no build step — it's one HTML file. Two easy options:

**Netlify Drop** (fastest, free)
1. Go to https://app.netlify.com/drop
2. Drag this whole folder onto the page
3. You get a live URL like `random-name-123.netlify.app` instantly

**GitHub Pages** (if you already use GitHub)
1. Create a new repo, upload these files
2. Repo Settings → Pages → deploy from the `main` branch, root folder
3. Live at `yourusername.github.io/reponame`

Either way: the page will be live and clickable, but marked "don't index me" — safe to share as a link for review/testing without it competing with the eventual real page in search results.

## What's still needed before this can be the real, live page
Per `mucho-record-build-spec.docx`, four things happen only once someone has Craft CMS access:
1. Footer link to `/record` added sitewide
2. Page actually served at `wearemucho.com/record` (ideally as a Twig template — see build spec Task 2, Option A)
3. Production `robots.txt` updated to allow `OAI-SearchBot`
4. `/record` added to the SEOmatic-managed sitemap

None of that is blocked by this staging deploy — this just gets the page live and testable in the meantime.

## Also still pending (content, not deployment)
Founding year, office addresses, founder quotes, award record links, FC Barcelona scope, corrections email, Wikidata entry, Clutch listing, engagement model statement — all marked in amber on the page itself. See `mucho-record-design-spec.docx` for the full sign-off list.

## Not included yet
Spanish version (`/es/registro`). Per the build spec, that's built after the English version is confirmed live — not part of this staging package.
