# Task: Gitignore user-specific data + create config.example.yaml

## Context

This repo is an AI-summarized YouTube briefing tool that's about to be open
sourced. Right now it works end-to-end for the original author, but two
things are currently committed that should not be:

1. **`config.yaml`** — contains 5 specific Korean channels hand-picked by
   the author. Anyone who forks inherits these channels instead of seeing
   an empty template.
2. **`data/briefings/*.json`** — contains 45 AI-generated Korean briefings
   the author has already produced. Anyone who forks clones 45 briefings
   about Korean economics they never asked for.

The fix is to gitignore both and ship a `config.example.yaml` template so
that new users have a clean starting point.

**This is the ONLY task in this spec.** Do not attempt multi-language
support, prompt refactoring, i18n, timezone parameterization, or any other
fork-friendliness work. That is explicitly out of scope and will be done
later as a separate effort. Keep the change small.

The goal: a stranger can `git clone` the repo, run `cp config.example.yaml
config.yaml`, edit it with their own channels, and run the pipeline — without
inheriting the original author's data.

## Ground rules

- **Preserve the author's local files.** My local `config.yaml` has my real
  channels and my local `data/briefings/` has 45 real briefings. Both must
  stay on my disk untouched. Only their tracked-in-git state changes.
- **No destructive git operations.** Use `git rm --cached` (keeps file on
  disk), never `git rm` alone.
- **Run the full test suite before committing.** All existing tests must
  still pass: `.venv/bin/python -m pytest tests/`.
- **Run `bun run build` before committing.** Astro reads briefings from
  disk at build time. The build must still succeed.
- **Do not reformat files you aren't changing.** No drive-by whitespace
  cleanups, no "while I'm here" edits.
- **Do not touch** `.env`, `NOTEBOOKLM_AUTH_JSON`, or any secret file. Those
  are already correctly gitignored.
- **Commit at the end of the task**, not in the middle. One commit for the
  whole task.

## Files to touch

1. `.gitignore` — add 3 entries
2. `data/briefings/.gitkeep` — new empty file
3. `config.example.yaml` — new file, copy of current `config.yaml` with
   channels replaced by a single placeholder
4. `config.yaml` — untrack from git, keep on disk unchanged
5. `data/briefings/*.json` — untrack from git, keep on disk unchanged
6. `README.md` — insert one short paragraph near the top

## Step-by-step

### Step 1 — Add entries to .gitignore

Open `.gitignore` and append the following block at the end (leave existing
entries alone):

```gitignore

# User-specific files — each fork has its own channels and briefings.
# config.yaml is the live config used by the pipeline. config.example.yaml
# is the tracked template new forks copy from.
config.yaml
data/briefings/*.json
!data/briefings/.gitkeep
```

The `!data/briefings/.gitkeep` line is important — it means "don't ignore
the .gitkeep file even though its parent pattern ignores JSON files in that
directory." This keeps the empty directory in git so the pipeline's
`mkdir(parents=True, exist_ok=True)` doesn't have to guess paths on first
run.

### Step 2 — Create data/briefings/.gitkeep

Create a new empty file at `data/briefings/.gitkeep`. Zero bytes, no
content. This is the standard pattern for keeping a directory in git.

### Step 3 — Create config.example.yaml

Read the current `config.yaml` in full. Create a new file
`config.example.yaml` that is a **byte-for-byte copy of `config.yaml`
EXCEPT** for the `channels:` section, which gets replaced with a generic
example.

Specifically, find the `channels:` block at the bottom of `config.yaml`
(currently 5 Korean channel entries for parkjonghoon / shuka /
understanding / jisik-inside / globelab) and replace the entire block
with exactly this:

```yaml
channels:
  # Add your channels here. To find a YouTube channel ID from a @handle:
  #   python scripts/resolve-channel-ids.py @your-channel-handle
  # Each entry needs:
  #   id:    the UCxxxxxxxxxxxxxxxxxxxxxx format channel ID
  #   name:  the channel's display name (used in briefing metadata + UI)
  #   slug:  a lowercase-alphanumeric short identifier, used in filenames
  #          and as the channel filter key in the frontend
  - id: "UCxxxxxxxxxxxxxxxxxxxxxx"
    name: "Example Channel"
    slug: example
```

Everything above the `channels:` section (the entire `pipeline:` block,
including all comments) should be preserved verbatim. Do not edit
defaults, do not reword comments, do not add or remove blank lines.

The goal is that `diff config.yaml config.example.yaml` shows ONLY the
channels block difference.

### Step 4 — Untrack config.yaml and briefing JSONs

Run these commands, in this order:

```bash
git rm --cached config.yaml
git rm --cached 'data/briefings/*.json'
```

Verify the files are still on disk after these commands:

```bash
ls -la config.yaml data/briefings/ | head
```

Expected: `config.yaml` exists, `data/briefings/` has many JSON files.
If either is missing, STOP and tell me — something went wrong.

### Step 5 — Add a short forking note to README.md

Open `README.md`. Find the first section heading after the `# YouTube
Briefing` title and the existing TL;DR paragraph. Insert the following
new section immediately after the TL;DR and before the next existing
heading:

```markdown
## Forking this project

This repo is designed to be forked. Each user brings their own channel
list and their own Gemini API key. The live `config.yaml` is gitignored,
so clones of upstream see a clean template at `config.example.yaml`.

To set up your own fork:

```bash
cp config.example.yaml config.yaml
```

Then edit `config.yaml` to list the YouTube channels you want to follow.
See `scripts/resolve-channel-ids.py` for converting `@handle` URLs to
channel IDs. See the "Setup" section below for secrets and deployment.

**Note on language:** The current version assumes Korean content and
Korean output. Multi-language support is planned but not yet shipped.
For now, forks should expect to read and modify the summarizer prompt
in `pipeline/summarizers/gemini_flash.py` if they want non-Korean output.
```

Preserve everything else in the README. Do not reword the TL;DR or any
existing section. The insertion should be the only change.

### Step 6 — Verify nothing is broken

Run these three commands in order and make sure all of them succeed:

```bash
.venv/bin/python -m pytest tests/
bun run build
git status
```

Expected results:

1. **pytest**: 167 tests pass. If any fail, stop and report which. Don't
   "fix" them by editing test files — that means the changes broke
   something real and need investigation.

2. **bun run build**: Astro build completes with the usual "X page(s)
   built" message. If it fails with "ENOENT" for a briefing file or
   similar, stop and report. (It shouldn't — the files are still on disk,
   just untracked.)

3. **git status**: Should show:
   - `.gitignore` — modified
   - `README.md` — modified
   - `config.example.yaml` — new file
   - `data/briefings/.gitkeep` — new file
   - `config.yaml` — deleted (because --cached removed it from the index;
     this is expected and intentional, the file still exists on disk)
   - `data/briefings/*.json` — all 45 shown as deleted (same reason)

   Do NOT commit yet — verify this list matches expected before proceeding.

### Step 7 — Commit

Stage and commit as a single atomic change:

```bash
git add .gitignore README.md config.example.yaml data/briefings/.gitkeep
git add -u config.yaml data/briefings/
git status  # verify the diff one more time
git commit -m "$(cat <<'EOF'
chore: gitignore user config + briefings, add config.example.yaml

This repo is intended to be forked. Move the live config.yaml and the
author's personal briefing JSONs out of git tracking so forks don't
inherit Korean-specific data. config.example.yaml is the clean template
new users copy from. Also adds a short "Forking this project" section
to the README.

Co-Authored-By: Codex <noreply@anthropic.com>
EOF
)"
```

After the commit:

```bash
git log -1 --stat
git status
```

`git log -1 --stat` should show: `.gitignore`, `README.md`,
`config.example.yaml`, `data/briefings/.gitkeep` as additions/modifications,
and `config.yaml` + 45 briefing JSONs as deletions from the index.

`git status` should be clean (working tree matches HEAD), but all the
previously-tracked-now-untracked files should still exist on disk if
you `ls`.

**Do NOT push.** I'll review the commit and push manually.

## Definition of done

Check all of these before reporting success:

- [ ] `.gitignore` has the 3 new lines appended
- [ ] `data/briefings/.gitkeep` exists and is empty
- [ ] `config.example.yaml` exists and differs from `config.yaml` ONLY in
      the `channels:` block
- [ ] `config.yaml` is untracked in git but still exists on disk with the
      author's 5 Korean channels intact
- [ ] `data/briefings/` still has ~45 `.json` files on disk, all untracked
- [ ] README has the new "Forking this project" section
- [ ] `.venv/bin/python -m pytest tests/` passes (167 tests)
- [ ] `bun run build` succeeds
- [ ] Exactly one commit has been made, with the message above
- [ ] Commit has not been pushed

## If something goes wrong

- If `git rm --cached` accidentally deletes a file from disk: `git checkout
  HEAD~1 -- <path>` to recover it, or copy from a fresh clone. Stop and
  report before continuing.
- If pytest fails after the changes: the changes shouldn't affect any
  tests. If something breaks, something is wrong with the working tree,
  not the tests. Run `git diff HEAD` to see what changed and report.
- If `bun run build` fails with a missing-briefing error: the Astro
  content collection might be configured to fail on empty directories.
  Stop and report the exact error.
- If you're not sure whether an intermediate step is correct: stop and
  ask me. Do not improvise, do not "while I'm here" any other fixes.
