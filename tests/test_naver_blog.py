from __future__ import annotations

from datetime import datetime, timezone

from pipeline.fetchers import naver_blog
from pipeline.fetchers.transcript_extractor import PermanentTranscriptFailure
from pipeline.models import DiscoverySource, SourceType


class _FakeResponse:
    def __init__(self, body: str):
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title><![CDATA[메르의 블로그]]></title>
    <image>
      <url><![CDATA[https://blogpfthumb.phinf.naver.net/profile.png]]></url>
    </image>
    <item>
      <title><![CDATA[최신 포스트]]></title>
      <link><![CDATA[https://blog.naver.com/ranto28/224250228854?fromRss=true]]></link>
      <guid>https://blog.naver.com/ranto28/224250228854</guid>
      <description><![CDATA[요약 미리보기 <img src="https://blogthumb.pstatic.net/cover1.png" />]]></description>
      <pubDate>Mon, 13 Apr 2026 07:05:47 +0900</pubDate>
    </item>
    <item>
      <title><![CDATA[이미 처리된 포스트]]></title>
      <guid>https://blog.naver.com/ranto28/224249713244</guid>
      <description><![CDATA[다른 글]]></description>
      <pubDate>Sun, 12 Apr 2026 07:05:47 +0900</pubDate>
    </item>
  </channel>
</rss>
"""


BLOG_HTML = """
<html>
  <head>
    <title>최신 포스트 : 네이버 블로그</title>
  </head>
  <body>
    <p class="blog_date">2026. 4. 13. 07:05</p>
    <div class="se-main-container">
      <div>
        <p>첫 문장입니다. 둘째 문장입니다. 셋째 문장입니다. 넷째 문장입니다.</p>
        <p>다섯째 문장입니다. 여섯째 문장입니다. 일곱째 문장입니다. 여덟째 문장입니다.</p>
        <p>아홉째 문장입니다. 열째 문장입니다. 열한째 문장입니다. 열두째 문장입니다.</p>
        <p>열셋째 문장입니다. 열넷째 문장입니다. 열다섯째 문장입니다. 열여섯째 문장입니다.</p>
      </div>
    </div>
  </body>
</html>
"""


class TestDiscoverNewBlogPosts:
    def test_parses_rss_items_into_video_meta(self, monkeypatch):
        monkeypatch.setattr(
            naver_blog.urllib.request,
            "urlopen",
            lambda request, timeout=20: _FakeResponse(RSS_XML),
        )

        posts = naver_blog.discover_new_blog_posts(
            blog_id="ranto28",
            channel_slug="mer",
            channel_name="메르의 블로그",
            known_video_ids={"224249713244"},
            max_new_posts=5,
        )

        assert len(posts) == 1
        post = posts[0]
        assert post.video_id == "224250228854"
        assert post.channel_id == "ranto28"
        assert post.channel_slug == "mer"
        assert post.source_type == SourceType.NAVER_BLOG
        assert str(post.source_url) == "https://blog.naver.com/ranto28/224250228854"
        assert str(post.thumbnail_url) == "https://blogthumb.pstatic.net/cover1.png"
        assert post.discovery_source == DiscoverySource.NAVER_BLOG_RSS
        assert post.duration_seconds == 0


class TestExtractBlogPostText:
    def test_extracts_main_text_from_mobile_page(self, monkeypatch):
        monkeypatch.setattr(
            naver_blog.urllib.request,
            "urlopen",
            lambda request, timeout=20: _FakeResponse(BLOG_HTML),
        )

        result = naver_blog.extract_blog_post_text(
            "https://blog.naver.com/ranto28/224250228854",
            item_id="224250228854",
        )

        assert result.source == "naver_blog_html"
        assert "첫 문장입니다." in result.text
        assert "열여섯째 문장입니다." in result.text
        assert result.published_at == datetime(2026, 4, 12, 22, 5, tzinfo=timezone.utc)

    def test_raises_permanent_failure_when_body_missing(self, monkeypatch):
        monkeypatch.setattr(
            naver_blog.urllib.request,
            "urlopen",
            lambda request, timeout=20: _FakeResponse("<html><body>본문 없음</body></html>"),
        )

        try:
            naver_blog.extract_blog_post_text(
                "https://blog.naver.com/ranto28/224250228854",
                item_id="224250228854",
            )
        except PermanentTranscriptFailure as exc:
            assert exc.failure_code == "empty_transcript"
        else:
            raise AssertionError("PermanentTranscriptFailure was not raised")

    def test_extracts_published_at_from_json_ld(self, monkeypatch):
        html = """
        <html>
          <head>
            <script type="application/ld+json">
              {"datePublished":"2026-04-13T07:05:47+09:00"}
            </script>
          </head>
          <body>
            <div class="se-main-container">
              <p>본문입니다. 본문입니다. 본문입니다. 본문입니다. 본문입니다.</p>
              <p>본문입니다. 본문입니다. 본문입니다. 본문입니다. 본문입니다.</p>
              <p>본문입니다. 본문입니다. 본문입니다. 본문입니다. 본문입니다.</p>
              <p>본문입니다. 본문입니다. 본문입니다. 본문입니다. 본문입니다.</p>
            </div>
          </body>
        </html>
        """
        monkeypatch.setattr(
            naver_blog.urllib.request,
            "urlopen",
            lambda request, timeout=20: _FakeResponse(html),
        )

        result = naver_blog.extract_blog_post_text(
            "https://blog.naver.com/ranto28/224250228854",
            item_id="224250228854",
        )

        assert result.published_at == datetime(2026, 4, 12, 22, 5, 47, tzinfo=timezone.utc)
