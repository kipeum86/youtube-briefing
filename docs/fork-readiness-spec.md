# Fork-Readiness Spec — for Codex

## Context

youtube-briefing is a personal tool that monitors 5 Korean YouTube channels and
publishes AI-summarized briefings as an Astro static site. It currently works
end-to-end for the original author, but the repo is going to be opened as
open source. A third party should be able to:

1. Fork the repo on GitHub
2. Set 2 secrets in their fork (`GEMINI_API_KEY` + `NOTEBOOKLM_AUTH_JSON`)
3. Edit `config.yaml` with their own channels and preferred language
4. Push → their own briefing site goes live on their own GitHub Pages

They should NOT have to touch Python code, prompt templates, Astro templates,
CSS, or test files to change language or channels. They should NOT inherit
my briefings, my channels, or any reference to my personal setup.

Right now the repo is tightly coupled to Korean + 5 specific Korean channels +
KST timezone + the author's specific Paperlogy font choice. This spec is the
work to decouple it.

Secrets are already safe: `.env` is gitignored, NotebookLM auth lives only
in GitHub Secrets, nothing personal is baked into committed code. That part
is done. This spec covers **everything else**.

## Ground rules for Codex

- **Preserve the existing tone of the codebase.** Editorial restraint. No
  emojis. Comments explain *why*, not *what*. Pydantic models are the
  schema source of truth. Atomic writes. Glob-based dedup. Don't invent
  new abstractions unless this spec says to.
- **Every task below must land with tests.** Pytest is configured with a
  60% coverage gate (`pyproject.toml`). Don't drop coverage. New helpers
  need direct unit tests, not just incidental coverage.
- **Run the full test suite before committing each task**:
  `.venv/bin/python -m pytest tests/`. Must show all green.
- **Run `bun run build` before committing any frontend change.** Must
  complete cleanly.
- **Commit after each task completes.** Message format:
  `<area>: <one-line summary>` then blank line then 2-4 sentence body
  explaining the motivation. Co-Authored-By line with the Codex model name
  and noreply address.
- **Do not skip pre-commit hooks.** If a hook fails, fix the underlying
  issue and re-commit. Never `--no-verify`.
- **Do not commit secrets, auth files, or briefing data.** If you see any
  file containing API keys, OAuth tokens, or channel-specific summaries,
  stop and tell me.
- **Do not regenerate `bun.lockb` or `requirements.txt`** unless a task
  explicitly adds a dependency.
- **Questions**: if any task below is ambiguous, implement the option
  that best matches the existing codebase's conventions. Don't try to be
  clever. If truly blocked, stop and ask.

## The 9 tasks, in dependency order

---

### Task 1 — Gitignore the user's briefings + local config

**Why:** Forks currently inherit my 45 briefing JSON files in `data/briefings/`
and my personal `config.yaml` with 5 specific Korean channels. Anyone who
forks starts with my data.

**Files:**
- `.gitignore` — add entries
- `data/briefings/.gitkeep` — new empty file so the directory survives
- `config.example.yaml` — new file, copy of current `config.yaml`
- `config.yaml` — remove from git tracking (but leave the file on my local
  disk; do NOT delete it from my filesystem)
- `README.md` — brief note about the example file pattern

**Implementation:**

1. Add to `.gitignore`:
   ```
   # User-specific — each fork has its own channels and briefings
   config.yaml
   data/briefings/*.json
   !data/briefings/.gitkeep
   ```

2. Create `data/briefings/.gitkeep` (empty file) so the directory still
   exists after the gitignore takes effect. The pipeline's `mkdir(parents=True,
   exist_ok=True)` already handles dir creation, but shipping the directory
   with the repo makes first-run more obvious.

3. Copy current `config.yaml` → `config.example.yaml`. Replace the 5 channels
   section with a single commented example showing the format:
   ```yaml
   channels:
     # Replace with your own channels. To find a channel ID:
     #   python scripts/resolve-channel-ids.py @handle-name
     - id: "UCxxxxxxxxxxxxxxxxxxxxxx"
       name: "Example Channel"
       slug: example
   ```

4. `git rm --cached config.yaml data/briefings/*.json` (keep the files on
   disk). Verify with `git status` that they're now untracked.

5. README.md — add a one-paragraph "Forking this project" section near the
   top (right after the TL;DR):
   > This project is designed to be forked. Every user brings their own
   > channels, their own API key, and their own publishing target. See
   > `config.example.yaml` for the config shape. Copy it to `config.yaml`
   > (gitignored) and edit to taste. See "Setup" below for secrets.

**Testing:** No new tests. Verify manually:
- `git status` after the task shows `config.yaml` and briefing JSONs as
  untracked (not modified, not deleted).
- `bun run build` still succeeds (Astro reads briefings from disk, which
  still exist locally).
- `.venv/bin/python -m pytest tests/` still passes.

**Commit:** `chore: gitignore config.yaml + briefings, add config.example.yaml`

---

### Task 2 — Introduce `language` + `timezone` into config shape

**Why:** Everything else downstream depends on these two values being
available. Add them first, with sensible defaults, before touching any
code that consumes them.

**Files:**
- `config.example.yaml` — add fields
- `config.yaml` — add fields (my local copy, uncommitted)
- `pipeline/run.py` — load and thread the values

**Implementation:**

1. In `config.example.yaml` under the `pipeline:` section, add:
   ```yaml
   # Output language. Controls the summary prompt language, the frontend
   # UI strings, and the language validator. Supported values:
   #   ko  — Korean (default, original project language)
   #   en  — English
   #   ja  — Japanese
   # To add a new language, create pipeline/summarizers/prompts/<code>.txt
   # and src/i18n/<code>.json, then add it here.
   language: ko

   # Timezone for filename dates and "next update" display text.
   # Any IANA tz name (Asia/Seoul, America/Los_Angeles, Europe/London, ...)
   timezone: Asia/Seoul
   ```

2. In `pipeline/run.py` `run()`, read both values:
   ```python
   language = pipeline_cfg.get("language", "ko")
   timezone_name = pipeline_cfg.get("timezone", "Asia/Seoul")
   ```
   Pass `language` into `load_summarizer()` as a new kwarg (next task will
   consume it). Pass `timezone_name` into `write_briefing()` call site as
   a new kwarg on `json_store.py` functions (task 6 consumes it).

3. In `pipeline/run.py` `load_config()`, validate that `language` is a
   string in a closed set: `{"ko", "en", "ja"}`. Raise `ValueError` with
   a helpful message if not. Validate that `timezone` is a valid IANA
   name by calling `ZoneInfo(value)` in a try/except.

**Testing:**
- `tests/test_run.py::TestLoadConfig` — add three tests:
  - `test_default_language_is_ko` — no language field in config, loads as "ko"
  - `test_invalid_language_raises` — `language: klingon` raises ValueError
  - `test_invalid_timezone_raises` — `timezone: Foo/Bar` raises ValueError
- Update the `_write_config` fixture to accept `language` and `timezone`
  kwargs (default to ko + Asia/Seoul).

**Commit:** `config: introduce language + timezone fields (default ko/KST)`

---

### Task 3 — Extract prompt template to per-language files

**Why:** `pipeline/summarizers/gemini_flash.py:32-85` hardcodes the entire
Korean prompt. Move it to `pipeline/summarizers/prompts/ko.txt` and load
by language code.

**Files:**
- `pipeline/summarizers/prompts/ko.txt` — new, extracted from current code
- `pipeline/summarizers/prompts/en.txt` — new, English version
- `pipeline/summarizers/prompts/ja.txt` — new, Japanese version
- `pipeline/summarizers/prompts/__init__.py` — new loader
- `pipeline/summarizers/gemini_flash.py` — consume loader
- `pipeline/summarizers/base.py` — thread `language` into summarizer init
- `tests/test_summarizers.py` — update

**Implementation:**

1. Create `pipeline/summarizers/prompts/ko.txt` by copy-pasting the current
   `PROMPT_TEMPLATE_V1` string from `gemini_flash.py` verbatim. Preserve
   the `{channel_name}`, `{title}`, `{transcript}` placeholders.

2. Create `pipeline/summarizers/prompts/en.txt`. This is a direct
   translation of the Korean prompt, preserving structure:
   - Role: "You are an expert summarizer of economics/current-affairs
     YouTube content."
   - Format: headline in `**...**` markdown bold, then three paragraphs
     (thesis / evidence / implications), blank lines between
   - Rules: no section labels like "1. Headline" in body, no meta
     narration like "this video discusses...", no hedge words unless the
     speaker hedged, preserve specific numbers and names
   - Target length: use `{min_chars}-{max_chars}` placeholders that the
     summarizer injects at runtime (see step 5). For English the target
     should feel right for the same information density as 700-1200
     Korean chars, which is roughly 1500-2500 English chars. Don't hardcode
     that range in the .txt — let it be templated.
   - Include a worked example: one headline + 3 paragraphs about a
     fictional Fed rate decision. Same editorial tone as the Korean example.

3. Create `pipeline/summarizers/prompts/ja.txt`. Direct translation to
   Japanese following the same structure. Target length range: roughly
   500-900 characters (Japanese is more info-dense than English or
   Korean). Again use templated range placeholders.

4. Create `pipeline/summarizers/prompts/__init__.py`:
   ```python
   """Per-language prompt template loader."""
   from pathlib import Path

   _PROMPTS_DIR = Path(__file__).parent

   def load_prompt_template(language: str) -> str:
       """Load the raw prompt text for a language code.

       Raises FileNotFoundError with a helpful message if the language
       has no template file."""
       path = _PROMPTS_DIR / f"{language}.txt"
       if not path.exists():
           raise FileNotFoundError(
               f"No prompt template for language={language!r}. "
               f"Create pipeline/summarizers/prompts/{language}.txt"
           )
       return path.read_text(encoding="utf-8")
   ```

5. Update `GeminiFlashSummarizer.__init__` to accept `language: str = "ko"`
   and store it. In `_build_prompt`, call `load_prompt_template(self.language)`
   instead of using the module-level constant. Add `{min_chars}` and
   `{max_chars}` to the `.format()` call alongside `channel_name`, `title`,
   `transcript`. Remove the old `PROMPT_TEMPLATE_V1` constant.

6. Update `Summarizer` base class in `base.py`: constructor accepts
   `language: str = "ko"`. `load_summarizer()` factory takes `language`
   as a new kwarg and passes it through.

**Testing:**
- `tests/test_summarizers.py::TestGeminiFlashSummarizer` — add:
  - `test_build_prompt_loads_ko_by_default` — verify the prompt text
    contains a string that only exists in `ko.txt` (e.g. `"한국어"`)
  - `test_build_prompt_loads_en_when_configured` — init with `language="en"`,
    verify prompt contains an English-only marker
  - `test_unknown_language_raises` — init with `language="klingon"`,
    calling `_build_prompt` raises `FileNotFoundError`
- `tests/test_summarizers.py::TestPromptLoader` — new class:
  - `test_load_ko` — returns non-empty string
  - `test_load_en` — returns non-empty string
  - `test_load_ja` — returns non-empty string
  - `test_load_missing_raises` — `load_prompt_template("nope")` raises

**Commit:** `summarizer: move prompt templates to per-language files`

---

### Task 4 — Parameterize language validator

**Why:** `pipeline/summarizers/base.py:141` `_validate_language` is hardcoded
to require 30% Hangul. English output would 100% fail this check. Make it
language-aware.

**Files:**
- `pipeline/summarizers/base.py` — rewrite `_validate_language` and `_is_hangul`
- `tests/test_summarizers.py` — expand language validation tests

**Implementation:**

1. Replace `_is_hangul` with a per-language script detection function:
   ```python
   def _script_ratio(text: str, language: str) -> float:
       """Fraction of non-space characters that match the target language's
       primary script.

       ko: Hangul syllables (U+AC00-U+D7A3) + jamo (U+1100-U+11FF)
       en: ASCII letters (a-z, A-Z)
       ja: Hiragana (U+3040-U+309F), Katakana (U+30A0-U+30FF), and CJK
           ideographs (U+4E00-U+9FFF) since Japanese mixes all three
       """
   ```
   Return 0.0 for empty input.

2. Update `_validate_language(self, text)` to use `self.language`:
   ```python
   ratio = _script_ratio(text, self.language)
   min_ratio = 0.3 if self.language in ("ko", "ja") else 0.5  # en needs
       # higher threshold because ASCII is everywhere (brand names, etc.)
   if ratio < min_ratio:
       raise PermanentSummarizerError(...)
   ```
   For English, keep the minimum at 0.5 — there will always be brand names
   and numbers, so "mostly ASCII" is the right threshold.

3. `_truncate_to_limit` also has Korean sentence boundaries hardcoded at
   line 168. Make this a lookup table:
   ```python
   _SENTENCE_BOUNDARIES = {
       "ko": ("다.", "요.", "음.", ".", "!", "?"),
       "en": (". ", "! ", "? "),
       "ja": ("。", "！", "？", "."),
   }
   ```
   Use `self.language` to pick the right list.

**Testing:**
- `tests/test_summarizers.py::TestValidateLanguage` — new class:
  - Test Hangul passes for `language="ko"`, fails for `language="en"`
  - Test ASCII English passes for `language="en"`, fails for `language="ko"`
  - Test mixed Japanese (hiragana+kanji) passes for `language="ja"`
  - Test empty string raises `summarizer_refused` regardless of language
  - Test English with 60% brand names + 40% prose passes (ratio 1.0, well
    above 0.5)
- `tests/test_summarizers.py::TestTruncate` — expand existing tests:
  - Add `test_truncate_en_at_sentence_boundary`
  - Add `test_truncate_ja_at_sentence_boundary`

**Commit:** `summarizer: parameterize language validator + sentence boundaries`

---

### Task 5 — Externalize frontend UI strings

**Why:** The Astro components have ~30 hardcoded Korean strings. Move them
all to `src/i18n/<lang>.json` and load at build time based on the config.

**Files:**
- `src/i18n/ko.json` — new, extracted from templates
- `src/i18n/en.json` — new, English translations
- `src/i18n/ja.json` — new, Japanese translations
- `src/lib/i18n.ts` — new, loader helper
- `src/lib/config.ts` — new, reads `config.yaml` at build time (if not
  already present)
- `src/layouts/Base.astro` — use i18n
- `src/pages/index.astro` — use i18n
- `src/pages/archive.astro` — use i18n
- `src/components/BriefingCard.astro` — use i18n
- `src/components/ChannelFilter.astro` — use i18n
- `src/components/EmptyState.astro` — use i18n

**Implementation:**

1. Create `src/lib/config.ts` (if it doesn't exist) that reads and parses
   `config.yaml` at build time:
   ```typescript
   import { readFileSync } from "node:fs";
   import { resolve } from "node:path";
   import yaml from "js-yaml";  // add to package.json

   interface PipelineConfig {
     language: string;
     timezone: string;
     // ...existing fields
   }

   export function loadConfig(): { pipeline: PipelineConfig; channels: any[] } {
     const path = resolve(process.cwd(), "config.yaml");
     return yaml.load(readFileSync(path, "utf8")) as any;
   }
   ```
   Add `js-yaml` and `@types/js-yaml` to `package.json` devDependencies.

2. Create `src/lib/i18n.ts`:
   ```typescript
   import { loadConfig } from "./config";

   type Strings = Record<string, string>;

   const STRINGS_BY_LANG: Record<string, Strings> = {
     ko: (await import("../i18n/ko.json")).default,
     en: (await import("../i18n/en.json")).default,
     ja: (await import("../i18n/ja.json")).default,
   };

   const config = loadConfig();
   const lang = config.pipeline.language ?? "ko";
   const strings = STRINGS_BY_LANG[lang] ?? STRINGS_BY_LANG.ko;

   export function t(key: string): string {
     const value = strings[key];
     if (value === undefined) {
       console.warn(`[i18n] missing key: ${key} (lang=${lang})`);
       return key;
     }
     return value;
   }

   export function getLang(): string {
     return lang;
   }
   ```

3. Create `src/i18n/ko.json` with every Korean string currently in the
   templates, keyed semantically. Required keys (scan the templates):
   ```json
   {
     "nav.recent": "최근",
     "nav.archive": "아카이브",
     "nav.main_label": "주 메뉴",
     "masthead.brand": "YOUTUBE BRIEFING",
     "card.expand": "펼쳐보기",
     "card.collapse": "접기",
     "card.source_video": "원본 영상",
     "card.failed_label": "요약 실패",
     "card.failed_body": "이 영상은 요약하지 못했습니다. 원본 영상에서 직접 확인하세요.",
     "card.failed_reason_prefix": "이유",
     "failure.session_expired": "일시적 인증 문제",
     "failure.video_removed": "영상이 삭제되었거나 비공개",
     "failure.members_only": "멤버십 전용 영상",
     "failure.age_restricted": "연령 제한 영상",
     "failure.empty_transcript": "음성 없음 또는 추출 불가",
     "failure.transcripts_disabled": "자막이 비활성화된 영상",
     "failure.summarizer_refused": "요약 처리 실패",
     "failure.wrong_language": "요약 언어 오류",
     "footer.next_update": "다음 업데이트 · 월·수·금 06:00 KST",
     "footer.publication": "YOUTUBE BRIEFING · {volNumber} · 2026",
     "footer.credit": "BUILT BY KIPEUM LEE · CLAUDE CODE + CODEX",
     "footer.github": "GITHUB ↗",
     "footer.github_aria": "View source on GitHub",
     "empty.title": "아직 브리핑이 없습니다",
     "empty.body": "파이프라인이 아직 실행되지 않았거나, 모든 영상이 필터링되었습니다.",
     "unread.counter": "{count}건의 새로운 브리핑",
     "archive.week_prefix": "WEEK ",
     "weekday.monday": "월",
     "weekday.tuesday": "화",
     "weekday.wednesday": "수",
     "weekday.thursday": "목",
     "weekday.friday": "금",
     "weekday.saturday": "토",
     "weekday.sunday": "일"
   }
   ```
   (Walk every `.astro` file and catch every Korean literal. The list
   above is my best guess from memory — verify against the actual files.)

4. Create `src/i18n/en.json` with English equivalents. Example entries:
   ```json
   {
     "nav.recent": "Recent",
     "nav.archive": "Archive",
     "nav.main_label": "Main menu",
     "masthead.brand": "YOUTUBE BRIEFING",
     "card.expand": "Expand",
     "card.collapse": "Collapse",
     "card.source_video": "Source video",
     "card.failed_label": "Summary failed",
     "card.failed_body": "This video could not be summarized. Watch the original instead.",
     "card.failed_reason_prefix": "Reason",
     "failure.session_expired": "Temporary auth issue",
     "failure.video_removed": "Video removed or private",
     "failure.members_only": "Members-only video",
     "failure.age_restricted": "Age-restricted",
     "failure.empty_transcript": "No audio or extraction failed",
     "failure.transcripts_disabled": "Transcripts disabled",
     "failure.summarizer_refused": "Summarization failed",
     "failure.wrong_language": "Wrong language in output",
     "footer.next_update": "Next update · Mon/Wed/Fri 06:00 {tzAbbrev}",
     "footer.publication": "YOUTUBE BRIEFING · {volNumber} · 2026",
     "footer.credit": "BUILT WITH YOUTUBE-BRIEFING",
     "footer.github": "GITHUB ↗",
     "footer.github_aria": "View source on GitHub",
     "empty.title": "No briefings yet",
     "empty.body": "The pipeline has not run yet, or all videos were filtered out.",
     "unread.counter": "{count} new briefing(s)",
     "archive.week_prefix": "WEEK ",
     "weekday.monday": "Mon",
     "weekday.tuesday": "Tue",
     "weekday.wednesday": "Wed",
     "weekday.thursday": "Thu",
     "weekday.friday": "Fri",
     "weekday.saturday": "Sat",
     "weekday.sunday": "Sun"
   }
   ```

5. Create `src/i18n/ja.json` with Japanese equivalents following the
   same schema.

6. Walk every `.astro` file and replace hardcoded Korean with `t("key")`
   calls. For strings that interpolate values (like "{count}건의 새로운
   브리핑"), do a simple `.replace("{count}", String(value))` at the call
   site.

7. **Important:** the `footer.credit` string should NOT include
   "BUILT BY KIPEUM LEE" for forks. My name belongs only in `ko.json`
   because that's the language of my personal fork. For `en.json` and
   `ja.json`, make it generic ("Built with youtube-briefing" / "youtube-
   briefingで構築") so forks don't accidentally credit me.

   Wait. Actually the cleanest approach: drop my name from all three.
   Make it "BUILT WITH YOUTUBE-BRIEFING" in all languages. Replace my
   personal credit with a link to the upstream repo. If I want to add my
   name back to MY local deployment, I can put it in my own local
   `config.yaml` as a string override. See step 8.

8. Add a `site.credit_override` config field for personal deployments:
   ```yaml
   site:
     credit_override: "BUILT BY KIPEUM LEE · CLAUDE CODE + CODEX"
   ```
   In the Base.astro footer, use `config.pipeline.site?.credit_override ??
   t("footer.credit")`. That way forks see the generic string by default,
   and I keep my byline in my local `config.yaml` (which is gitignored).

**Testing:**
- No unit tests for `.astro` files (Astro doesn't have a great test story).
- Add a build-verification script:
  `scripts/verify-i18n.sh`:
  ```bash
  #!/bin/bash
  # Verify every key in ko.json exists in en.json and ja.json
  bun run --silent scripts/verify-i18n.ts
  ```
  And `scripts/verify-i18n.ts`:
  ```typescript
  import ko from "../src/i18n/ko.json";
  import en from "../src/i18n/en.json";
  import ja from "../src/i18n/ja.json";

  const koKeys = new Set(Object.keys(ko));
  const missing = {
    en: [...koKeys].filter(k => !(k in en)),
    ja: [...koKeys].filter(k => !(k in ja)),
  };
  if (missing.en.length || missing.ja.length) {
    console.error("Missing i18n keys:", missing);
    process.exit(1);
  }
  console.log(`✓ all ${koKeys.size} keys present in en.json and ja.json`);
  ```
  Run this in CI (add a step to `.github/workflows/deploy-pages.yml`).

- Build with `language: ko`, assert the generated HTML contains "펼쳐보기"
- Build with `language: en`, assert it contains "Expand"
- Build with `language: ja`, assert it contains the Japanese equivalent

  You can do this inside a pytest file at `tests/test_i18n_build.py` that
  shells out to `bun run build` with different configs. Or skip and rely
  on manual verification + the key-parity check.

**Commit:** `frontend: externalize UI strings to src/i18n/{ko,en,ja}.json`

---

### Task 6 — Parameterize timezone in json_store and BriefingCard

**Why:** `pipeline/writers/json_store.py:32` hardcodes `KST = ZoneInfo("Asia/Seoul")`.
Filename dates and frontend date formatting all assume KST.

**Files:**
- `pipeline/writers/json_store.py` — accept timezone as parameter
- `pipeline/run.py` — pass timezone through
- `src/components/BriefingCard.astro` — use config timezone
- `src/lib/config.ts` — expose timezone
- `tests/test_json_store.py` — add timezone tests

**Implementation:**

1. `briefing_filename(briefing, timezone_name="Asia/Seoul")` — new kwarg.
   Default preserves current behavior for unchanged tests.

2. `write_briefing(briefing, briefings_dir, timezone_name="Asia/Seoul")` —
   pass through to `briefing_filename`.

3. `run.py` passes `timezone_name=timezone_name` to `write_briefing`.

4. `BriefingCard.astro` — `formatKstDate` is renamed `formatLocalDate` and
   accepts a timezone param from config. Use JavaScript's `Intl.DateTimeFormat`
   with `timeZone` option instead of the manual UTC+9 offset math:
   ```typescript
   import { loadConfig } from "../lib/config";
   const tz = loadConfig().pipeline.timezone ?? "Asia/Seoul";

   function formatLocalDate(iso: Date): string {
     const fmt = new Intl.DateTimeFormat("en-CA", {
       timeZone: tz,
       year: "numeric",
       month: "2-digit",
       day: "2-digit",
     });
     return fmt.format(iso).replaceAll("-", ".");
   }
   ```

5. The existing `KST` constant in `json_store.py` can stay as a default,
   or be removed entirely. Remove it.

**Testing:**
- `tests/test_json_store.py::TestBriefingFilename` — add:
  - `test_timezone_la_produces_different_date_than_seoul` — same
    `published_at`, two different filenames when timezone differs
  - `test_default_timezone_is_seoul_for_back_compat` — calling without
    the kwarg gives the same result as before
  - `test_write_briefing_honors_timezone_kwarg` — full round-trip

**Commit:** `filesystem+frontend: parameterize briefing timezone`

---

### Task 7 — Decouple font-family via CSS custom property

**Why:** `src/styles/global.css:70` hardcodes `"Paperlogy", "Apple SD Gothic
Neo", "Noto Sans KR"`. Non-Korean forks shouldn't inherit Paperlogy.

**Files:**
- `src/styles/global.css` — use `var(--font-family)`
- `src/layouts/Base.astro` — inline `<style>` block that sets `--font-family`
  from config
- `config.example.yaml` — add `site.font_family` field
- `public/fonts/README.md` — new, brief note on where to put custom fonts

**Implementation:**

1. In `global.css`, change `body { font-family: ... }` to
   `body { font-family: var(--font-family, "Inter", system-ui, sans-serif); }`.
   Do the same for any other place font-family is set. Also change
   `.briefing .full-summary-headline` if it sets font-family (it does —
   line 481 or so).

2. In `Base.astro` `<head>`, add a `<style is:global>` block that injects
   the custom property:
   ```astro
   ---
   import { loadConfig } from "../lib/config";
   const config = loadConfig();
   const fontFamily = config.pipeline.site?.font_family ?? '"Inter", system-ui, sans-serif';
   ---
   <style is:global set:html={`:root { --font-family: ${fontFamily}; }`}></style>
   ```
   Be careful about quoting. The value needs to be a valid CSS font-family
   declaration. For Paperlogy (what I use), the value is:
   `'"Paperlogy", "Apple SD Gothic Neo", "Noto Sans KR", sans-serif'`
   (double-quoted font names inside single-quoted string).

3. `config.example.yaml` gains:
   ```yaml
   site:
     # CSS font-family value. Must be a valid font-family declaration,
     # including any fallback stack. Defaults to Inter + system sans.
     # If you specify a custom font here, drop the font files in
     # public/fonts/ and add @font-face rules to src/styles/global.css.
     font_family: '"Inter", system-ui, sans-serif'
   ```

4. The existing `@font-face` rules for Paperlogy at the top of `global.css`
   should STAY. They don't hurt anything — if the font file exists in
   public/fonts, it loads; if not, the browser silently skips and falls
   through to the next stack entry.

5. My local `config.yaml` (uncommitted) should have:
   ```yaml
   site:
     font_family: '"Paperlogy", "Apple SD Gothic Neo", "Noto Sans KR", sans-serif'
     credit_override: "BUILT BY KIPEUM LEE · CLAUDE CODE + CODEX"
   ```
   so my personal site looks exactly like it does now.

6. Create `public/fonts/README.md`:
   ```markdown
   # Custom fonts

   Drop `.woff2` files here and add matching `@font-face` rules at the
   top of `src/styles/global.css`. Then reference the font family in
   your `config.yaml` under `site.font_family`.

   The repo includes Paperlogy (Korean) as the original author's choice.
   You can delete those files if you don't need them.
   ```

**Testing:** Manual only.
- Build with default config → body font should fall through to
  `Inter, system-ui`.
- Build with my local config → body font should be Paperlogy (visual check).

**Commit:** `frontend: decouple font-family via CSS custom property`

---

### Task 8 — Scrub "Korean" from README + add fork guide

**Why:** README tagline says "Auto-summarized Korean economics & current-
affairs YouTube". That needs to become language-neutral, and a Fork
section needs step-by-step instructions.

**Files:**
- `README.md` — rewrite header + add sections

**Implementation:**

Rewrite the first 40 lines of README.md to:

```markdown
# YouTube Briefing

_An editorial feed of AI-summarized YouTube videos, in the language you choose._

**TL;DR.** A personal tool that watches a list of YouTube channels, pulls the
latest uploads on a schedule, extracts transcripts, summarizes with Gemini
Flash, and publishes the result as a static Astro site. Designed to be
forked — each user brings their own channels, their own API key, and their
own language.

Supports Korean, English, and Japanese out of the box. Adding a new
language is a matter of dropping two files (see "Adding a language" below).

## Live example

<!-- keep existing link to kipeum86.github.io/youtube-briefing -->

## Fork and deploy in 6 steps

1. **Fork this repo on GitHub.** Click the Fork button at the top.

2. **Clone your fork locally** and install dependencies:
   ```bash
   git clone https://github.com/YOUR-USERNAME/youtube-briefing.git
   cd youtube-briefing
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   bun install
   ```

3. **Create your config.** Copy the example and edit:
   ```bash
   cp config.example.yaml config.yaml
   ```
   In `config.yaml`, set `language`, `timezone`, and the `channels:` list
   with your own channels. To convert a `@handle` URL to a channel ID:
   ```bash
   python scripts/resolve-channel-ids.py @your-channel-handle
   ```

4. **Get a Gemini API key** at https://aistudio.google.com/apikey (free
   tier is enough for daily runs). In your fork's repo settings → Secrets
   and variables → Actions, add a secret named `GEMINI_API_KEY`.

5. **Set up NotebookLM auth** (required for CI because YouTube blocks cloud
   runner IPs from anonymous transcript APIs):
   ```bash
   pip install notebooklm-py
   notebooklm login
   gh secret set NOTEBOOKLM_AUTH_JSON -R YOUR-USERNAME/youtube-briefing \
     < ~/.notebooklm/storage_state.json
   ```
   The session refreshes automatically for a few weeks, then you rerun
   `notebooklm login` and re-upload the secret.

6. **Enable GitHub Pages** in your repo settings (Source: GitHub Actions)
   and **push.** The first scheduled run (Mon/Wed/Fri 06:00 in your
   configured timezone) will populate the site, or you can trigger a
   manual run from the Actions tab.

## Adding a language

1. Create `pipeline/summarizers/prompts/<code>.txt` using `ko.txt` or
   `en.txt` as a starting template.
2. Create `src/i18n/<code>.json` with all the keys from `ko.json`.
3. Set `language: <code>` in your `config.yaml`.
4. Run `bun run scripts/verify-i18n.ts` to confirm you covered every key.

## What the author ships

The upstream `main` branch uses Korean + 5 specific Korean economics
channels because that's what the original author reads. Use
`config.example.yaml` as your clean starting point, not `config.yaml` from
upstream.
```

Keep the existing "Architecture", "Failure contract", "Testing", and other
internal sections below this rewritten header — don't touch them.

**Testing:** None. Human review.

**Commit:** `docs: rewrite README for fork + multi-language`

---

### Task 9 — CI: run the i18n key-parity check

**Why:** Task 5 ships the verify-i18n.ts script. CI should fail if a
contributor adds a new string to `ko.json` without adding it to `en.json`
and `ja.json`.

**Files:**
- `.github/workflows/deploy-pages.yml` — add a step

**Implementation:**

Add a step right before the `astro build` step:

```yaml
- name: Verify i18n key parity
  run: bun run scripts/verify-i18n.ts
```

**Testing:** Push the commit and verify the workflow runs green. To
force a failure once for sanity, temporarily delete one key from en.json
and confirm CI catches it, then restore.

**Commit:** `ci: fail build when i18n keys drift between languages`

---

## Order of operations (one-liner)

1. Task 1 — gitignore data + config.example.yaml
2. Task 2 — config fields for language + timezone
3. Task 3 — per-language prompt files
4. Task 4 — parameterized language validator
5. Task 5 — externalized i18n strings
6. Task 6 — parameterized timezone
7. Task 7 — CSS font custom property
8. Task 8 — README rewrite
9. Task 9 — CI key parity check

Each task commits on its own. Nine commits total. Run the full test
suite + `bun run build` after each. If any task reveals a problem this
spec didn't anticipate, stop and tell me before improvising.

## Out of scope — do NOT do these

- Don't rewrite the summarizer retry logic or the failure classification
  system. They're correct and tested.
- Don't touch `pipeline/fetchers/transcript_extractor.py` — 3-tier fallback
  is tuned and working.
- Don't change the Pydantic models schema. If a new field is needed,
  ask first.
- Don't introduce a new framework (i18next, react-intl, etc). The
  hand-rolled `t()` function is enough for ~40 strings.
- Don't regenerate briefings. The existing 45 Korean briefings in my
  local `data/briefings/` are my personal data and should survive the
  gitignore move untouched.
- Don't reformat files beyond the lines you're actually changing. No
  drive-by whitespace cleanups.

## Definition of done

- `git status` on a fresh clone shows `config.yaml` untracked, no briefing
  JSONs tracked.
- Setting `language: en` in `config.yaml` and running the pipeline
  produces English briefings.
- Setting `language: ja` produces Japanese briefings.
- Setting `language: ko` produces Korean briefings identical to current
  output (regression proof).
- `bun run build` succeeds in all three language configurations.
- `.venv/bin/python -m pytest tests/` passes with ≥ 60% coverage.
- `bun run scripts/verify-i18n.ts` passes.
- README's "Fork and deploy in 6 steps" walks a stranger from zero to
  live site with no Python or Astro edits.
