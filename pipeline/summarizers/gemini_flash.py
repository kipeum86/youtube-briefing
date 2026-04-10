"""Gemini Flash summarizer — concrete Summarizer implementation.

Uses google-genai SDK. The prompt is Korean-native and enforces the
"심층 분석 톤" shape documented in the plan:

    [주제 한 줄] (15자 이내)
    [핵심 주장 2-3문장]
    [근거/데이터 3-5문장]
    [함의 1-2문장]

Retry policy: delegated to the parent Summarizer.summarize() loop for
short-output retries. Network/5xx retries are handled inside _call_api
via two attempts with 5s backoff (transient classification).
"""

from __future__ import annotations

import logging
import os
import time

from pipeline.models import VideoMeta
from pipeline.summarizers.base import (
    PermanentSummarizerError,
    Summarizer,
    TransientSummarizerError,
)

logger = logging.getLogger(__name__)


PROMPT_TEMPLATE_V1 = """당신은 한국어 경제·시사 콘텐츠 전문 요약가입니다.

다음은 유튜브 채널 "{channel_name}"의 영상 "{title}"에서 추출한 트랜스크립트입니다. 이 영상의 핵심 내용을 700~1,200자 한국어로 요약해 주세요.

## 출력 형식 (정확히 따를 것)

첫 줄: **주제 한 줄** (양쪽에 별표 두 개씩, 15자 이내, 문장 아닌 개조식)
빈 줄
다음 단락: 핵심 주장 3~4문장 — 영상이 말하려는 테제를 직설적으로
빈 줄
다음 단락: 근거/데이터 4~6문장 — 구체적인 숫자, 사례, 인용, 정책 언급. 영상이 제시한 통계는 반드시 포함
빈 줄
다음 단락: 함의 2~3문장 — 이 이야기가 왜 지금 중요한가, 다음 관전 포인트는 무엇인가

## 출력 규칙

- "1. 주제 한 줄", "2. 핵심 주장", "**핵심 주장**" 같은 **섹션 라벨/번호를 본문에 절대 출력하지 말 것**. 라벨 없이 곧장 내용만 써라.
- 첫 줄의 주제 헤드라인만 `**...**` 마크다운 별표를 쓴다. 본문 단락에는 `**...**` 강조를 쓰지 마라.
- 단락 사이에는 반드시 빈 줄(`\\n\\n`)을 두어 분리한다.
- 마크다운 헤더(`#`, `##`)나 글머리표(`-`, `*`)를 쓰지 마라. 평문 단락만.

## 톤과 내용

- "이 영상은 ~에 대해 이야기합니다" 같은 메타 설명 금지
- "~일 수도 있습니다", "~라고 생각합니다" 같은 헷지 표현 금지 (영상이 그렇게 말하지 않는 한)
- 화자가 말하지 않은 내용의 추측 금지
- 영어 단어로 도피 금지 (경제 용어는 한국어로, 고유명사만 영문 허용)
- 화자의 확신 수준을 그대로 반영 (약하게 만들지 말 것)
- 구체 숫자, 회사명, 정책 이름을 원문에 있다면 반드시 포함
- 문어체, 냉정, 저널리즘 톤

## 좋은 예시

```
**美 연준 금리인하 시그널의 함정**

파월 의장이 75bp 인하를 예고했지만, 시장이 이를 기정사실로 받아들인 순간부터 장기 금리는 오히려 상승하기 시작했다. 슈카월드는 이 역설이 단순한 수급 문제가 아니라 인플레이션 기대 부활의 결과라고 본다. 결국 인하 신호 자체가 경기 과열을 재점화시키는 자기파괴적 사이클이 작동 중이다.

도트 플롯에서 2025년 말 정책금리 중앙값은 3.4%로 6개월 만에 50bp 하향됐다. 그런데 같은 기간 10년물 국채금리는 3.8%에서 4.3%로 50bp 상승했다. 달러 인덱스도 102에서 106으로 반등했다. 1995년 그린스펀의 연착륙 국면 초기에 동일한 패턴이 관찰됐는데, 그때도 인하 사이클이 끝까지 가지 못하고 6개월 만에 동결로 전환했다.

이번 인하 사이클은 과거와 달리 주식시장에 지속적인 상승 모멘텀을 제공하기 어렵다. 단기 트레이딩 관점에서는 변동성 확대에 대비한 포지션 관리가 핵심이며, 다음 관전 포인트는 12월 FOMC 점도표 수정 폭이다.
```

위 예시처럼 라벨 없이 단락만 출력해라.

---

트랜스크립트:

{transcript}

---

위 트랜스크립트의 700~1,200자 한국어 심층 요약:"""


class GeminiFlashSummarizer(Summarizer):
    """Gemini Flash implementation of the Summarizer contract.

    Loads the `google-genai` library lazily so the rest of the pipeline
    can run without it installed (e.g. tests that mock this class).
    """

    provider = "gemini"

    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        prompt_version: str = "v1",
        api_key: str | None = None,
    ):
        self.model = model
        self.prompt_version = prompt_version
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "").strip()
        self._client = None  # lazy init

    def _build_prompt(self, transcript: str, meta: VideoMeta) -> str:
        if self.prompt_version != "v1":
            raise ValueError(
                f"unknown prompt_version for GeminiFlashSummarizer: {self.prompt_version}"
            )
        # Cap the transcript portion of the prompt so we stay well under
        # Gemini Flash's input context. Long 언더스탠딩 videos can exceed 100K chars.
        capped = transcript if len(transcript) < 80_000 else transcript[:80_000]
        return PROMPT_TEMPLATE_V1.format(
            channel_name=meta.channel_name,
            title=meta.title,
            transcript=capped,
        )

    def _call_api(self, prompt: str) -> str:
        """Call the Gemini API with 2-attempt retry on transient failures.

        Transient: network errors, 429, 5xx
        Permanent: 401, 403, invalid request
        """
        if not self._api_key:
            raise PermanentSummarizerError(
                "GEMINI_API_KEY is not set (check .env)",
                failure_code="summarizer_refused",
            )

        client = self._get_client()

        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                )
                text = getattr(response, "text", None) or ""
                if text.strip():
                    return text
                logger.warning("gemini returned empty text on attempt %d", attempt + 1)
                last_exc = PermanentSummarizerError(
                    "gemini returned empty response",
                    failure_code="summarizer_refused",
                )
            except PermanentSummarizerError:
                raise
            except Exception as e:  # noqa: BLE001
                classified = _classify_gemini_exception(e)
                if not classified.transient:
                    raise PermanentSummarizerError(
                        classified.message,
                        failure_code=classified.failure_code,
                    ) from e
                last_exc = e
                logger.warning(
                    "gemini transient failure attempt %d: %s — retrying in 5s",
                    attempt + 1,
                    e,
                )
                time.sleep(5)

        raise TransientSummarizerError(
            f"gemini failed after 2 attempts: {last_exc}"
        ) from last_exc

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from google import genai
        except ImportError as e:
            raise PermanentSummarizerError(
                "google-genai library not installed",
                failure_code="summarizer_refused",
            ) from e
        self._client = genai.Client(api_key=self._api_key)
        return self._client


class _Classified:
    def __init__(self, message: str, transient: bool, failure_code: str = "summarizer_refused"):
        self.message = message
        self.transient = transient
        self.failure_code = failure_code


def _classify_gemini_exception(exc: Exception) -> _Classified:
    """Map google-genai exceptions to transient/permanent classification."""
    name = type(exc).__name__
    msg = str(exc).lower()

    # Transient: retry next attempt
    if "429" in msg or "rate" in msg and "limit" in msg:
        return _Classified(f"rate limit: {exc}", transient=True)
    if "timeout" in name.lower() or "timeout" in msg:
        return _Classified(f"timeout: {exc}", transient=True)
    if "500" in msg or "502" in msg or "503" in msg or "504" in msg:
        return _Classified(f"5xx: {exc}", transient=True)
    if "connection" in msg or "network" in msg or "refused" in msg:
        return _Classified(f"network: {exc}", transient=True)

    # Permanent: don't retry
    if "401" in msg or "403" in msg or "unauthorized" in msg or "permission" in msg:
        return _Classified(f"auth: {exc}", transient=False, failure_code="summarizer_refused")
    if "400" in msg or "invalid" in msg or "bad request" in msg:
        return _Classified(f"invalid request: {exc}", transient=False, failure_code="summarizer_refused")

    # Unknown → transient (safer default)
    return _Classified(f"unknown gemini error: {name}: {exc}", transient=True)
