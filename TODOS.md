# TODOS

Deferred work captured during `/plan-eng-review` and `/plan-design-review` on 2026-04-09.
These are explicitly NOT v1 scope. Revisit after initial launch and ~1 month of real usage.

---

## 1. oEmbed fallback for `thumbnail_url` 404s

**What:** When `https://i.ytimg.com/vi/{video-id}/hqdefault.jpg` returns 404 (rare but possible on very old or private-then-republished videos), fall back to YouTube's oEmbed endpoint to resolve the actual thumbnail URL.

**Why:** v1 hardcodes `hqdefault.jpg` which is >99% reliable. A real 404 would render a broken image on the briefing card, which looks amateur hour.

**Pros:**
- Eliminates a rare but real visual break
- oEmbed is stable, auth-free, no rate limits at this volume

**Cons:**
- Adds one network call per briefing during pipeline run (~50ms overhead per video × 10 videos = 500ms — negligible)
- More code to test

**Context:** Briefing JSON schema sets `thumbnail_url` to the hardcoded `hqdefault.jpg` pattern at write time in `pipeline/writers/json_store.py`. The fallback would be a pre-write HEAD request: if 404, call `https://www.youtube.com/oembed?url={video_url}&format=json` and extract `thumbnail_url` from the response.

**Depends on / blocked by:** None. Can be added any time after v1 ships.

---

## 2. RSS output feed for the briefing site itself

**What:** Generate `/rss.xml` as an Astro route that emits the 20 most-recent briefings in RSS 2.0 format. Each `<item>` includes the summary (HTML-escaped) and the original YouTube link.

**Why:** Lets friends and LinkedIn visitors subscribe via any RSS reader. Also positions the site as "I built a feed of feeds" which is a stronger narrative than "yet another dashboard."

**Pros:**
- Low effort (~30 lines in `src/pages/rss.xml.ts`)
- Zero additional infra
- Classic web primitive — fits the "no database, no accounts, just files" aesthetic

**Cons:**
- Unused by 95% of visitors
- Introduces one more public surface to maintain

**Context:** Astro has a first-class `@astrojs/rss` package. Implementation is a single file that reads the same `getCollection('briefings')` the index page uses, maps to RSS item format, and returns. The `<link>` rel in Base.astro can advertise the feed for auto-discovery.

**Depends on / blocked by:** v1 site must be live and stable first.

---

## 3. NotebookLM-py session expiry monitoring + proactive warning

**What:** Detect when the NotebookLM-py session cookie is approaching expiration (or when recent runs show increasing auth failures) and emit a macOS notification reminding the user to re-authenticate BEFORE the next scheduled run.

**Why:** The single biggest operational risk is the session silently expiring and the pipeline starting to fail. Currently the user only finds out when they check the site (or the logs) and notice no new briefings. Proactive warning shortens the mean-time-to-recovery.

**Pros:**
- Turns a silent failure into a visible one
- No new infra — uses existing logging + `osascript` notification pattern from `commit-and-push.sh`

**Cons:**
- Requires figuring out HOW to detect session expiry before it happens (cookie expiry date? sliding window of recent failures? empirical research needed)
- NotebookLM-py may not expose session metadata directly

**Context:** Need to research NotebookLM-py's session handling. Two possible signals: (a) if the library exposes session cookie expiry, check on every run start and notify if <48 hours remain; (b) if not, track the rolling rate of `AuthFailure` exceptions in `logs/pipeline.log` — if last 3 runs all had auth failures, notify.

**Depends on / blocked by:** v1 running in production for ~1 month so we have failure rate baseline.

---

## 4. Systematic prompt iteration + retroactive re-summarization

**What:** After the first month of real briefings, if the current prompt (prompt_version v1) is underperforming on specific channel types or topics, iterate the prompt to v2. Delete all existing briefing JSONs, re-run the pipeline to regenerate from cached local transcripts (no NotebookLM round-trips — transcripts are cached).

**Why:** Prompts are the product. v1 is a first draft locked under time pressure. Real usage will reveal failure modes the spot-check in Step 5 couldn't catch.

**Pros:**
- Transcripts are cached locally so re-summarization is cheap and fast (only Gemini calls, no NotebookLM)
- Explicit prompt versioning (`prompt_version` field in JSON schema) makes the change auditable
- Forces continuous quality improvement loop

**Cons:**
- 50+ briefings × Gemini calls = ~$0.50 per re-summarization batch (still negligible)
- Risk of overfitting the prompt to the specific channels — what worked for 5 Korean econ channels might not work if the channel list changes
- Requires enough baseline data to evaluate before/after

**Context:** Process: (1) write a new prompt in `pipeline/summarizers/gemini_flash.py` tagged `prompt_version: v2`, (2) run `scripts/re-summarize-all.sh` which deletes `data/briefings/*.json` and reruns `pipeline/run.py`, (3) compare outputs on a sample manually, (4) commit.

**Depends on / blocked by:** At least 1 month of v1 data to evaluate against. v1 must be stable first.

---

## 5. "Last updated" indicator on the site header

**What:** Show a small "오늘 업데이트됨 · 2026-04-09" or "마지막 업데이트: 2일 전" indicator at the top of the index page. Derive from the max `generated_at` across all briefings in the collection.

**Why:** LinkedIn visitors landing on the site need a signal that it's alive and maintained. "When did this last run?" is the first question. Without an answer, it looks like abandonware.

**Pros:**
- One line of Astro template code + a CSS badge
- High information density for the pixel cost
- Matches the "honest personal tool" positioning — we're not hiding how recent the data is

**Cons:**
- Could make a short outage look worse than it is ("3 days ago" during a pipeline issue)

**Context:** In `src/pages/index.astro`, compute `lastUpdated = briefings[0].generated_at` (assuming briefings are sorted descending by generated_at). Render in the header as a subtle gray badge near the title. Format relative: "오늘", "어제", "N일 전" for <7 days, then date for older.

**Depends on / blocked by:** None. Can be added any time.

---

## 6. Entry-point card fade-in animation (stagger)

**What:** On initial page load, briefing cards fade in sequentially with an 80ms stagger. Uses `animation: fadeInUp 300ms ease-out both` applied per card via a `style="animation-delay: calc(var(--i) * 80ms)"` inline variable.

**Why:** Currently cards appear instantly — the site feels static. A subtle stagger-in gives the feed a "living" signal on first paint. Signals activity without being showy.

**Pros:**
- Adds emotional warmth to the 5-second first impression
- Very cheap (pure CSS keyframes, no JS)
- Respects `prefers-reduced-motion: reduce` via the existing blanket rule

**Cons:**
- 80ms × ~10 cards = 800ms of "waiting" on slow devices, can feel laggy
- Breaks the instant-load feel of SSG
- Easy to do badly

**Context:** Add to `src/components/BriefingCard.astro` via `--i` CSS variable passed as index prop. Only animates on first visit per session (use `sessionStorage` flag to prevent re-animating on nav back). Don't stagger more than 5 cards — after that all appear together.

**Depends on / blocked by:** v1 shipping first. This is polish.

---

## 7. System dark mode support

**What:** Respect `@media (prefers-color-scheme: dark)` and swap the palette to a dark variant. Ink `#1a1a1a` → warm paper cream (like `#e8e4d8`), bg cream `#faf8f4` → warm slate (like `#1a1820`), accent deep forest → brighter sage (like `#6a9a80`). Paperlogy stays. Left rail inverts.

**Why:** Korean users browsing in dim rooms or late at night prefer dark mode. A cream-on-dark "warm slate" version fits the editorial aesthetic without becoming a generic dark dashboard.

**Pros:**
- Respects OS preference (no manual toggle needed)
- Warm palette distinction from generic dark-mode apps
- Long-form reading comfort in low light

**Cons:**
- Doubles the palette decisions (every token needs a dark counterpart)
- Contrast ratios need re-verification on the dark side
- Risks becoming the thing you wanted to avoid (Variant C territory)
- Adds ~200 lines of CSS

**Context:** Add `@media (prefers-color-scheme: dark) { :root { ... } }` overriding color tokens only. Keep typography + layout identical. Test contrast on all 4 text levels. Verify the deep forest accent reads on the warm slate bg.

**Depends on / blocked by:** v1 light mode proven first. At least 2 weeks of real usage.

---

## 8. Weekly lead briefing 2x emphasis

**What:** Each week, the first briefing (newest) gets a visually larger treatment: H2 at `clamp(32px, 6vw, 48px)` instead of `clamp(26px, 4.5vw, 38px)`, preview renders in full (3 lines not 2), optional subtle accent background tint. Creates a "magazine lead story" feel.

**Why:** Current design treats every briefing uniformly. Adding a visual "lead" reinforces the weekly editorial rhythm and gives the feed a natural reading entry point. Suggested by the design-review subagent as a portfolio differentiator option.

**Pros:**
- Strengthens the magazine-spread metaphor already established by the left rail
- Creates a clear visual "start here" for new visitors
- Cheap (one CSS class toggle on the first card)

**Cons:**
- Requires deciding what counts as "the lead" — newest? user-picked? most-important by some heuristic?
- Could feel arbitrary if the newest isn't actually the most interesting that week
- One more spec to maintain

**Context:** Add a `.briefing.lead` class applied to the first card in the feed (newest by date). Only applies on `/` (index), not `/archive`. CSS overrides font size + preview line count. Optional: add a small "이번 주 주요 브리핑" eyebrow label above the h2.

**Depends on / blocked by:** v1 shipping first. Evaluate after 2 weeks — does the lead card feel earned or arbitrary?
