# YouTube Briefing

_Auto-summarized Korean economics & current-affairs YouTube, as an editorial feed._

**TL;DR (English).** A personal tool that watches 5 Korean YouTube channels
(박종훈, 슈카월드, 언더스탠딩, 지식인사이드, 지구본연구소), extracts transcripts,
generates 500–1,000-character Korean deep-analysis summaries with Gemini Flash,
and publishes them as a static Astro site on GitHub Pages. Updates Mon/Wed/Fri at
06:00 KST via a local `launchd` timer. No database, no Sheets, no Google Cloud.
Fork-friendly: clone, add your Gemini API key, edit the channel list, run the pipeline.

---

## 뭘 하는 건가

바쁠 때 경제·시사 유튜브를 볼 시간이 없어서 만든 개인용 브리핑 툴.
월·수·금 아침마다 다섯 개 채널의 새 영상 트랜스크립트를 자동 추출하고,
500–1,000자 한국어 심층 요약으로 정리해서 에디토리얼 피드로 보여준다.

- **타겟 채널:** 박종훈의 지식한방, 슈카월드, 언더스탠딩, 지식 인사이드, 지구본연구소
- **업데이트:** 주 3회 (Mon/Wed/Fri 06:00 KST)
- **스택:** Python 파이프라인 (로컬 launchd) + Astro 정적 사이트 (GitHub Pages)
- **저장소:** JSON 파일 in git (Google Sheets, DB 없음)

## 설정 (7단계)

1. **클론**
   ```bash
   git clone https://github.com/YOUR_USERNAME/youtube-briefing
   cd youtube-briefing
   ```

2. **의존성 설치**
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   npm install
   brew install yt-dlp  # macOS, or apt / choco equivalent
   ```

3. **Gemini API 키 설정**
   ```bash
   cp .env.example .env
   # .env 파일 열고 GEMINI_API_KEY= 뒤에 키 붙이기
   # 키 발급: https://aistudio.google.com/apikey (무료)
   ```

4. **채널 설정** — `config.yaml`의 각 채널 `id` 필드를 채운다
   ```bash
   python scripts/resolve-channel-ids.py https://www.youtube.com/@syukaworld
   # 출력: UCxxxxxxxxxxxxxxxx
   ```

5. **(선택) NotebookLM 폴백** — 대부분 사용자는 건너뛰어도 됨.
   `youtube-transcript-api`로 대부분의 영상이 커버됨. NotebookLM은
   자막이 없는 영상에 대한 백업 경로.

6. **첫 실행**
   ```bash
   python pipeline/run.py
   # data/briefings/*.json 파일 생성되는지 확인
   npm run dev
   # localhost:4321 에서 사이트 확인
   ```

7. **(선택) 자동화** — Mon/Wed/Fri 06:00 KST에 자동 실행
   ```bash
   ./scripts/install-launchd.sh  # macOS
   ./scripts/install-cron.sh     # Linux
   ```

## 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│  Local (your Mac)                                            │
│                                                              │
│  launchd (Mon/Wed/Fri 06:00 KST)                            │
│       │                                                      │
│       ▼                                                      │
│  pipeline/run.py                                            │
│       │                                                      │
│       ├─ fetchers/discovery.py  (YouTube RSS → yt-dlp       │
│       │                          catchup)                    │
│       │                                                      │
│       ├─ fetchers/transcript_extractor.py                   │
│       │   ├─ youtube-transcript-api (primary)               │
│       │   ├─ notebooklm-py (optional fallback)              │
│       │   └─ yt-dlp VTT (last resort)                       │
│       │                                                      │
│       ├─ summarizers/gemini_flash.py                        │
│       │                                                      │
│       └─ writers/json_store.py                              │
│              │                                                │
│              ▼                                                │
│       data/briefings/*.json  (committed)                    │
│       data/transcripts/*.txt (gitignored)                   │
│              │                                                │
│       scripts/commit-and-push.sh                             │
│              │                                                │
└──────────────┼───────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│  GitHub                                                      │
│                                                              │
│  .github/workflows/pages.yml                                 │
│       │                                                      │
│       ▼                                                      │
│  astro build → GitHub Pages deploy                           │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## 디자인

- **타이포:** Paperlogy (한국어 geometric sans, Black + Medium)
- **팔레트:** Ink `#1a1a1a` on cream `#faf8f4` + deep forest `#2d4a3e` accent
- **레이아웃:** 720px editorial column + desktop 120px 좌측 이슈 레일
- **완전 접근성:** WCAG AA 대비, 키보드 네비, `:focus-visible`, skip link, reduced motion

자세한 내용은 `.gstack/projects/youtube-briefing/` 에 있는 디자인 문서 참조.

## 라이선스

Apache 2.0 — see [LICENSE](./LICENSE).
