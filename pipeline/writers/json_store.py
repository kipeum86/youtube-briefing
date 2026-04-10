"""Briefing JSON writer — Pydantic-validated, atomic, glob-based dedup.

Key invariants:
  1. Every write goes through `Briefing.model_validate()`, so schema drift
     fails at write time in-process instead of silently corrupting data.
  2. Writes are atomic: write to `{name}.json.tmp` then `os.replace()` to the
     final path. No partial files.
  3. Dedup is stateless — `list_processed_video_ids()` derives the "already
     processed" set by globbing `data/briefings/*.json` and parsing video_id
     from each filename. There is NO separate state.json (per plan P1 revision).

Filename format: `{YYYY-MM-DD}-{channel_slug}-{video_id}.json`
  - date: from `published_at`, YYYY-MM-DD (KST)
  - channel_slug: lowercase, alphanumeric + hyphen
  - video_id: 5-20 chars, alphanumeric + `_`/`-`
"""

from __future__ import annotations

import logging
import os
import re
from datetime import timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from pipeline.models import Briefing

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

# Filename pattern: 2026-04-09-shuka-abc123XYZ45.json
# Groups: date, slug, video_id
_FILENAME_PATTERN = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})-(?P<slug>[a-z][a-z0-9-]*)-(?P<video_id>[A-Za-z0-9_-]{5,20})\.json$"
)


def briefing_filename(briefing: Briefing) -> str:
    """Compute the canonical filename for a briefing."""
    date_kst = briefing.published_at.astimezone(KST).strftime("%Y-%m-%d")
    return f"{date_kst}-{briefing.channel_slug}-{briefing.video_id}.json"


def write_briefing(briefing: Briefing, briefings_dir: Path | str) -> Path:
    """Write a briefing to disk atomically, validating on the way in.

    Idempotent: if the target file already exists, logs a warning and
    overwrites it. The caller is responsible for checking
    `list_processed_video_ids()` before calling this if dedup is desired.

    Returns:
        The final path the briefing was written to.

    Raises:
        pydantic.ValidationError: if the briefing object fails schema validation
        OSError: on filesystem errors (disk full, permissions, etc.)
    """
    # Re-validate even though `briefing` is already a Briefing — defense in depth
    # against in-place mutations between construction and write.
    validated = Briefing.model_validate(briefing.model_dump())

    briefings_dir = Path(briefings_dir)
    briefings_dir.mkdir(parents=True, exist_ok=True)

    final_path = briefings_dir / briefing_filename(validated)
    tmp_path = final_path.with_suffix(final_path.suffix + ".tmp")

    if final_path.exists():
        logger.info(
            "overwriting existing briefing %s (this is expected for re-summarization)",
            final_path.name,
        )

    json_bytes = validated.model_dump_json(indent=2).encode("utf-8")
    tmp_path.write_bytes(json_bytes)
    os.replace(tmp_path, final_path)

    logger.info(
        "wrote briefing %s (status=%s, %d bytes)",
        final_path.name,
        validated.status.value,
        len(json_bytes),
    )
    return final_path


def list_processed_video_ids(briefings_dir: Path | str) -> set[str]:
    """Return the set of video_ids already present in briefings_dir.

    This is the ONLY source of truth for "what's already processed" — there
    is no separate state.json. Derivation is stateless and O(n) in directory
    size, which is fine for ~1500 files/year.

    Files with malformed names are ignored (with a warning) — they will not
    prevent discovery of new videos.
    """
    briefings_dir = Path(briefings_dir)
    if not briefings_dir.exists():
        return set()

    ids: set[str] = set()
    for entry in briefings_dir.iterdir():
        if not entry.is_file() or entry.suffix != ".json":
            continue
        match = _FILENAME_PATTERN.match(entry.name)
        if not match:
            logger.warning("ignoring malformed briefing filename: %s", entry.name)
            continue
        ids.add(match.group("video_id"))

    return ids


def list_processed_video_ids_by_channel(
    briefings_dir: Path | str,
) -> dict[str, set[str]]:
    """Return a per-channel mapping of processed video_ids.

    Used by the discovery saturation check, which needs to know which videos
    a SPECIFIC channel has already processed — not the global set across all
    channels. The global set causes false-positive saturation: if any channel
    has any processed videos, every other channel's "no match in RSS" check
    fires saturation, triggering needless yt-dlp catchup.

    Example:
        {
          "shuka":         {"NPL-NrvvK_w"},
          "parkjonghoon":  {"abc123XYZ45", "def456..."},
          "understanding": set(),
        }
    """
    briefings_dir = Path(briefings_dir)
    result: dict[str, set[str]] = {}
    if not briefings_dir.exists():
        return result

    for entry in briefings_dir.iterdir():
        if not entry.is_file() or entry.suffix != ".json":
            continue
        match = _FILENAME_PATTERN.match(entry.name)
        if not match:
            continue
        slug = match.group("slug")
        result.setdefault(slug, set()).add(match.group("video_id"))

    return result


def iter_briefings(briefings_dir: Path | str) -> Iterable[Briefing]:
    """Yield all briefings in the directory, sorted newest-first by filename.

    Used by the frontend build step (if we ever need to bulk-validate all
    briefings before Astro builds) and by tests. Does NOT materialize a list
    so the caller can stop early.
    """
    briefings_dir = Path(briefings_dir)
    if not briefings_dir.exists():
        return

    files = sorted(
        (f for f in briefings_dir.iterdir() if f.is_file() and f.suffix == ".json"),
        reverse=True,  # Newer filenames (2026-04-09-...) sort after older ones
    )
    for f in files:
        try:
            yield Briefing.model_validate_json(f.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.error("failed to load briefing %s: %s", f.name, e)
            continue
