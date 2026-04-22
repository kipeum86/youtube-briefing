"""Naver blog discovery + full-post extraction.

Uses the blog's public RSS feed for discovery and the mobile post page
(`m.blog.naver.com`) for body extraction. This keeps the acquisition path
deterministic and lightweight: no API key, no browser automation, no iframe
parsing from the desktop page.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from zoneinfo import ZoneInfo

from pipeline.fetchers.discovery import DiscoveryFailure
from pipeline.fetchers.transcript_extractor import (
    PermanentTranscriptFailure,
    TranscriptResult,
    TransientTranscriptFailure,
)
from pipeline.models import DiscoverySource, SourceType, VideoMeta

logger = logging.getLogger(__name__)

RSS_URL_TEMPLATE = "https://rss.blog.naver.com/{blog_id}.xml"
DEFAULT_THUMBNAIL = "https://ssl.pstatic.net/static/blog/icon/favicon.ico"

DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko,en-US;q=0.9,en;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
}

MOBILE_HEADERS = {
    **DEFAULT_HEADERS,
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
}

TAG_RE = re.compile(r"<[^>]+>")
IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"', re.IGNORECASE)
PC_BLOG_RE = re.compile(r"^https?://blog\.naver\.com/")
BLOG_POST_RE = re.compile(r"blog\.naver\.com/([a-zA-Z0-9_]+)/(\d+)")
BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
BLOCK_END_RE = re.compile(r"</(p|div|li|h[1-6])>", re.IGNORECASE)
WHITESPACE_RE = re.compile(r"[ \t]+")
BLANK_LINES_RE = re.compile(r"\n{3,}")
SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)
JSON_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
META_CONTENT_RE = re.compile(
    r"<meta[^>]+(?:property|name|itemprop)=[\"'](?P<key>[^\"']+)[\"'][^>]+content=[\"'](?P<value>[^\"']+)[\"']",
    re.IGNORECASE,
)
NAVER_DATE_RE = re.compile(
    r'(?:class=["\'][^"\']*\b(?:se_publishDate|blog_date)\b[^"\']*["\']|id=["\']_postAddDate["\'])[^>]*>(.*?)<',
    re.DOTALL | re.IGNORECASE,
)
KEYED_TIMESTAMP_RE = re.compile(
    r"(datePublished|published|regDate|addDate|writeDate|createdAt|created|updatedAt|modify|modified)"
    r"[^0-9]{0,30}(20\d{10}|20\d{12})",
    re.IGNORECASE,
)
ISO_WITH_TZ_RE = re.compile(
    r"\b(20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+\-]\d{2}:?\d{2}))\b"
)
ISO_NO_TZ_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?)\b")
DOT_DATE_RE = re.compile(
    r"(20\d{2})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?"
    r"(?:\s*(오전|오후)?\s*(\d{1,2}):(\d{2})(?::(\d{2}))?)?"
)
KOREAN_DATE_RE = re.compile(
    r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"
    r"(?:\s*(오전|오후)?\s*(\d{1,2}):(\d{2})(?::(\d{2}))?)?"
)
TIMESTAMP_14_RE = re.compile(r"\b(20\d{12})\b")
TIMESTAMP_12_RE = re.compile(r"\b(20\d{10})\b")
KST = ZoneInfo("Asia/Seoul")


def discover_new_blog_posts(
    blog_id: str,
    channel_slug: str,
    channel_name: str,
    known_video_ids: set[str],
    max_new_posts: int | None = None,
) -> list[VideoMeta]:
    """Discover new posts from a Naver blog via RSS."""
    rss_url = RSS_URL_TEMPLATE.format(blog_id=urllib.parse.quote(blog_id))
    request = urllib.request.Request(rss_url, headers=DEFAULT_HEADERS)

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            xml_text = response.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as exc:
        raise DiscoveryFailure(
            f"[{channel_slug}] Naver blog RSS returned HTTP {exc.code}: {rss_url}"
        ) from exc
    except urllib.error.URLError as exc:
        raise DiscoveryFailure(
            f"[{channel_slug}] Naver blog RSS network failure: {exc.reason}"
        ) from exc

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise DiscoveryFailure(f"[{channel_slug}] Naver blog RSS XML parse failure: {exc}") from exc

    channel_el = root.find("channel")
    if channel_el is None:
        raise DiscoveryFailure(f"[{channel_slug}] Naver blog RSS missing <channel> element")

    feed_thumbnail = _clean_text(channel_el.findtext("image/url")) or DEFAULT_THUMBNAIL

    posts: list[VideoMeta] = []
    for item_el in channel_el.findall("item"):
        try:
            meta = _parse_rss_item(
                item_el=item_el,
                blog_id=blog_id,
                channel_slug=channel_slug,
                channel_name=channel_name,
                feed_thumbnail=feed_thumbnail,
            )
        except ValueError as exc:
            logger.warning("[%s] skipping malformed Naver RSS item: %s", channel_slug, exc)
            continue

        if meta.video_id not in known_video_ids:
            posts.append(meta)

    if max_new_posts is not None:
        posts = posts[:max_new_posts]

    logger.info("[%s] Naver blog RSS discovery: %d new post(s)", channel_slug, len(posts))
    return posts


def extract_blog_post_text(post_url: str, item_id: str) -> TranscriptResult:
    """Read the full body of a Naver blog post from the mobile page."""
    mobile_url = _to_mobile_url(post_url)
    request = urllib.request.Request(mobile_url, headers=MOBILE_HEADERS)

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            html = response.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as exc:
        if exc.code in {404, 410}:
            raise PermanentTranscriptFailure(
                item_id,
                f"Naver blog post unavailable ({exc.code})",
                failure_code="video_removed",
            ) from exc
        if exc.code in {401, 403}:
            raise PermanentTranscriptFailure(
                item_id,
                f"Naver blog post access restricted ({exc.code})",
                failure_code="empty_transcript",
            ) from exc
        raise TransientTranscriptFailure(item_id, f"Naver blog HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise TransientTranscriptFailure(item_id, f"Naver blog network failure: {exc.reason}") from exc

    content_area = _extract_content_area(html)
    content = _extract_text(content_area)
    published_at = _extract_published_at(html)

    if len(content) < 100:
        title = _extract_title(html)
        detail = f" ({title})" if title else ""
        raise PermanentTranscriptFailure(
            item_id,
            f"Naver blog body extraction produced too little text{detail}",
            failure_code="empty_transcript",
        )

    return TranscriptResult(text=content, source="naver_blog_html", published_at=published_at)


def _parse_rss_item(
    item_el: ET.Element,
    blog_id: str,
    channel_slug: str,
    channel_name: str,
    feed_thumbnail: str,
) -> VideoMeta:
    title = _clean_text(item_el.findtext("title"))
    raw_url = _clean_text(item_el.findtext("guid")) or _clean_text(item_el.findtext("link"))
    if not raw_url:
        raise ValueError("missing item URL")

    source_url = _canonical_blog_url(raw_url)
    match = BLOG_POST_RE.search(source_url)
    if not match:
        raise ValueError(f"could not extract post id from URL: {source_url}")

    post_id = match.group(2)
    pub_date = _clean_text(item_el.findtext("pubDate"))
    if not pub_date:
        raise ValueError(f"missing pubDate for {source_url}")

    published_at = parsedate_to_datetime(pub_date).astimezone(timezone.utc)
    description = item_el.findtext("description") or ""
    thumbnail = _extract_first_image(description) or feed_thumbnail or DEFAULT_THUMBNAIL

    return VideoMeta(
        video_id=post_id,
        channel_id=blog_id,
        channel_slug=channel_slug,
        channel_name=channel_name,
        title=title,
        published_at=published_at,
        discovery_source=DiscoverySource.NAVER_BLOG_RSS,
        source_type=SourceType.NAVER_BLOG,
        source_url=source_url,
        thumbnail_url=thumbnail,
        duration_seconds=0,
    )


def _canonical_blog_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    clean = parsed._replace(query="", fragment="")
    return urllib.parse.urlunsplit(clean)


def _to_mobile_url(url: str) -> str:
    url = url.strip()
    url = PC_BLOG_RE.sub("https://m.blog.naver.com/", url)
    if not url.startswith("https://m.blog.naver.com/"):
        match = BLOG_POST_RE.search(url)
        if match:
            url = f"https://m.blog.naver.com/{match.group(1)}/{match.group(2)}"
    return _canonical_blog_url(url)


def _extract_title(html: str) -> str:
    match = TITLE_RE.search(html)
    if not match:
        return ""
    title = unescape(TAG_RE.sub("", match.group(1))).strip()
    return re.sub(r"\s*[-:|]?\s*네이버\s*블로그$", "", title).strip()


def _extract_published_at(html: str) -> datetime | None:
    """Extract the post's actual published_at from the page HTML when possible."""
    decoded = unescape(html)
    head_window = decoded[:20000]

    for script_match in JSON_LD_RE.finditer(decoded):
        json_text = script_match.group(1).strip()
        if not json_text:
            continue
        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError:
            continue
        found = _find_date_in_json(payload)
        parsed = _parse_blog_datetime(found)
        if parsed is not None:
            return parsed

    meta_candidates = []
    for meta_match in META_CONTENT_RE.finditer(head_window):
        key = meta_match.group("key").strip().lower()
        if key in {
            "article:published_time",
            "article:modified_time",
            "og:published_time",
            "og:updated_time",
            "date",
            "publish_date",
            "datepublished",
            "datecreated",
        }:
            meta_candidates.append(meta_match.group("value"))

    for candidate in meta_candidates:
        parsed = _parse_blog_datetime(candidate)
        if parsed is not None:
            return parsed

    naver_date_match = NAVER_DATE_RE.search(decoded)
    if naver_date_match:
        parsed = _parse_blog_datetime(naver_date_match.group(1))
        if parsed is not None:
            return parsed

    keyed_match = KEYED_TIMESTAMP_RE.search(head_window)
    if keyed_match:
        parsed = _parse_blog_datetime(keyed_match.group(2))
        if parsed is not None:
            return parsed

    for re_obj in (ISO_WITH_TZ_RE, ISO_NO_TZ_RE, DOT_DATE_RE, KOREAN_DATE_RE, TIMESTAMP_14_RE, TIMESTAMP_12_RE):
        match = re_obj.search(head_window)
        if not match:
            continue
        parsed = _parse_blog_datetime(match.group(0))
        if parsed is not None:
            return parsed

    return None


def _find_date_in_json(payload: object) -> str | None:
    if isinstance(payload, list):
        for item in payload:
            found = _find_date_in_json(item)
            if found:
                return found
        return None

    if not isinstance(payload, dict):
        return None

    for key in ("datePublished", "dateCreated", "uploadDate", "dateModified"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    graph = payload.get("@graph")
    if graph is not None:
        found = _find_date_in_json(graph)
        if found:
            return found

    for value in payload.values():
        if isinstance(value, (list, dict)):
            found = _find_date_in_json(value)
            if found:
                return found

    return None


def _parse_blog_datetime(raw: str | None) -> datetime | None:
    if raw is None:
        return None

    cleaned = WHITESPACE_RE.sub(" ", TAG_RE.sub("", unescape(raw))).strip()
    if not cleaned:
        return None

    for full_match, fmt in (
        (TIMESTAMP_14_RE.fullmatch(cleaned), "%Y%m%d%H%M%S"),
        (TIMESTAMP_12_RE.fullmatch(cleaned), "%Y%m%d%H%M"),
    ):
        if full_match:
            try:
                dt = datetime.strptime(full_match.group(1), fmt)
            except ValueError:
                return None
            return dt.replace(tzinfo=KST).astimezone(timezone.utc)

    if ISO_WITH_TZ_RE.fullmatch(cleaned):
        try:
            return datetime.fromisoformat(cleaned.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None

    if ISO_NO_TZ_RE.fullmatch(cleaned):
        try:
            dt = datetime.fromisoformat(cleaned.replace(" ", "T"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(timezone.utc)

    for re_obj in (DOT_DATE_RE, KOREAN_DATE_RE):
        match = re_obj.fullmatch(cleaned)
        if not match:
            continue
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))
        meridiem = match.group(4)
        hour = int(match.group(5) or 0)
        minute = int(match.group(6) or 0)
        second = int(match.group(7) or 0)
        if meridiem == "오후" and hour < 12:
            hour += 12
        if meridiem == "오전" and hour == 12:
            hour = 0
        try:
            dt = datetime(year, month, day, hour, minute, second, tzinfo=KST)
        except ValueError:
            return None
        return dt.astimezone(timezone.utc)

    return None


def _extract_content_area(html: str) -> str:
    cleaned = SCRIPT_STYLE_RE.sub("", html)

    for marker in (
        r'class="[^"]*\bse-main-container\b[^"]*"',
        r'class="[^"]*\bpost_ct\b[^"]*"',
        r'class="[^"]*\bpostViewArea\b[^"]*"',
        r'class="[^"]*\bpost-view\b[^"]*"',
    ):
        match = re.search(marker, cleaned)
        if match:
            return _extract_div_block(cleaned, match.start())

    marker = cleaned.find('id="viewTypeSelector"')
    if marker >= 0:
        return _extract_div_block(cleaned, marker)

    return ""


def _extract_div_block(html: str, start_pos: int) -> str:
    tag_start = html.rfind("<div", 0, start_pos)
    if tag_start < 0:
        tag_start = start_pos

    depth = 0
    pos = tag_start
    started = False
    length = len(html)
    while pos < length:
        if html[pos : pos + 4] == "<!--":
            end = html.find("-->", pos + 4)
            pos = end + 3 if end >= 0 else length
            continue
        if html[pos : pos + 4] == "<div" and (pos + 4 >= length or html[pos + 4] in (" ", ">", "\t", "\n", "/")):
            depth += 1
            started = True
        elif html[pos : pos + 6] == "</div>":
            depth -= 1
            if started and depth == 0:
                return html[tag_start : pos + 6]
        pos += 1

    return html[tag_start:]


def _extract_text(html_fragment: str) -> str:
    text = BR_RE.sub("\n", html_fragment)
    text = BLOCK_END_RE.sub("\n", text)
    text = TAG_RE.sub("", text)
    text = unescape(text)

    lines = []
    for line in text.split("\n"):
        stripped = WHITESPACE_RE.sub(" ", line).strip()
        if stripped:
            lines.append(stripped)

    result = "\n".join(lines)
    result = BLANK_LINES_RE.sub("\n\n", result)
    return result.strip()


def _extract_first_image(html_fragment: str) -> str | None:
    match = IMG_RE.search(html_fragment)
    if not match:
        return None
    return unescape(match.group(1)).strip()


def _clean_text(value: str | None) -> str:
    return (value or "").strip()
