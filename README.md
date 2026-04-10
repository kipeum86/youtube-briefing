# YouTube Briefing

_Auto-summarized Korean economics & current-affairs YouTube, as an editorial feed._

**TL;DR (English).** A personal tool that watches 5 Korean YouTube channels
(박종훈, 슈카월드, 언더스탠딩, 지식인사이드, 지구본연구소), extracts transcripts,
generates 700–1,200-character Korean deep-analysis summaries with Gemini Flash,
and publishes them as a static Astro site on GitHub Pages. Updates Mon/Wed/Fri at
06:00 KST via a local `launchd` timer. No database, no Sheets, no Google Cloud.
Fork-friendly: clone, add your Gemini API key, edit the channel list, run the pipeline.

---

## 뭘 하는 건가

바쁠 때 경제·시사 유튜브를 볼 시간이 없어서 만든 개인용 브리핑 툴.
월·수·금 아침마다 다섯 개 채널의 새 영상 트랜스크립트를 자동 추출하고,
700–1,200자 한국어 심층 요약으로 정리해서 에디토리얼 피드로 보여준다.

- **타겟 채널:** 박종훈의 지식한방, 슈카월드, 언더스탠딩, 지식 인사이드, 지구본연구소
- **업데이트:** 주 3회 (Mon/Wed/Fri 06:00 KST, 로컬 launchd)
- **스택:** Python 파이프라인 (로컬 Mac + launchd) + Astro 정적 사이트 (GitHub Pages)
- **저장소:** JSON 파일 in git (Google Sheets, DB 없음)

> **왜 로컬 실행인가?** 처음엔 GitHub Actions 로 돌리려 했는데, YouTube 가
> 클라우드 runner IP 전체를 차단함 (`youtube-transcript-api → IpBlocked`,
> `yt-dlp → HTTP 429`). 유일하게 작동하는 경로는 로그인된 Google 세션 기반의
> NotebookLM 이라서, 파이프라인이 네 Mac 에서 돌아야 함. Gate 0 에서 실측 검증.

## 설정 (7단계)

1. **포크 또는 클론**
   ```bash
   git clone https://github.com/YOUR_USERNAME/youtube-briefing
   cd youtube-briefing
   ```

2. **의존성 설치**
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   npm install
   brew install yt-dlp           # macOS (Linux: pipx install yt-dlp)
   ```

3. **Gemini API 키**
   ```bash
   cp .env.example .env
   # .env 열고 GEMINI_API_KEY= 뒤에 키 붙여넣기
   # 키 발급: https://aistudio.google.com/apikey (무료)
   ```

4. **NotebookLM 로그인** — 이게 primary 트랜스크립트 소스니까 필수
   ```bash
   notebooklm login
   # → 브라우저가 열림 → Google 로그인 → 세션이
   #   ~/.notebooklm/storage_state.json 에 저장됨
   # 세션은 수 주 단위로 만료. 실패하기 시작하면 재실행.
   ```

5. **채널 ID 조회 + config.yaml 수정**
   ```bash
   python scripts/resolve-channel-ids.py https://www.youtube.com/@syukaworld
   # 출력: UCxxxxxxxxxxxxxxxx — 이걸 config.yaml 각 채널 id: 에 붙여넣기
   ```

6. **첫 실행 + 미리보기**
   ```bash
   # 작은 스모크 테스트 먼저
   python pipeline/run.py --only-channel shuka --limit 1

   # 성공하면 전체 실행 (첫 실행은 ~50분, 75개 영상)
   python pipeline/run.py

   # 미리보기
   npm run dev    # localhost:4321
   ```

7. **자동화 설치** — Mon/Wed/Fri 06:00 KST 에 자동 실행
   ```bash
   ./scripts/install-launchd.sh   # macOS
   ./scripts/install-cron.sh      # Linux
   ```

   설치 후:
   - launchd 상태 확인: `launchctl list | grep youtube-briefing`
   - 강제 즉시 실행: `launchctl start com.kpsfamily.youtube-briefing`
   - 로그 확인: `tail -f logs/pipeline.log`
   - 일시 정지: `launchctl unload ~/Library/LaunchAgents/com.kpsfamily.youtube-briefing.plist`

## 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│  LOCAL (your Mac)                                            │
│                                                              │
│  launchd timer (Mon/Wed/Fri 06:00 KST)                       │
│       │                                                      │
│       ▼                                                      │
│  pipeline/run.py                                             │
│       │                                                      │
│       ├─ fetchers/discovery.py  (YouTube RSS → yt-dlp        │
│       │                          catchup)                    │
│       │                                                      │
│       ├─ fetchers/transcript_extractor.py (3-tier)           │
│       │   ├─ tier 1: notebooklm-py (PRIMARY, required)       │
│       │   │          uses ~/.notebooklm/storage_state.json   │
│       │   ├─ tier 2: youtube-transcript-api (safety net)     │
│       │   └─ tier 3: yt-dlp VTT (safety net)                 │
│       │                                                      │
│       ├─ summarizers/gemini_flash.py                         │
│       │   (700-1,200 Korean chars, prompt v1)                │
│       │                                                      │
│       └─ writers/json_store.py                               │
│              │                                                │
│              ▼                                                │
│       data/briefings/*.json  (ok + failed placeholders)      │
│       data/transcripts/*.txt (cached, gitignored)            │
│              │                                                │
│       scripts/commit-and-push.sh                             │
│              │                                                │
└──────────────┼───────────────────────────────────────────────┘
               │ git push origin main
               ▼
┌─────────────────────────────────────────────────────────────┐
│  GitHub (thin CI, only for deploy)                           │
│                                                              │
│  .github/workflows/pages.yml                                 │
│       │                                                      │
│       ▼                                                      │
│  astro build → GitHub Pages deploy                           │
│       │                                                      │
│       ▼                                                      │
│  https://USERNAME.github.io/youtube-briefing/                │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 트러블슈팅

- **`NotebookLM session file not found`** → `notebooklm login` 다시 실행
- **`NotebookLM session expired`** → 같음, 세션이 몇 주마다 만료됨
- **모든 tier 가 transient 실패** → YouTube 자체 문제, 잠시 후 재시도
- **`GEMINI_API_KEY is not set`** → `.env` 확인, venv 활성화 확인
- **맥이 꺼져 있어서 scheduled 실행 놓침** → 다음 실행에서 RSS catchup + yt-dlp fallback 으로 자동 복구 (새 영상 15개 이하까지는)
- **파이프라인 로그** → `tail -f logs/pipeline.log`
- **launchd 로그** → `tail -f logs/launchd.out logs/launchd.err`

## 디자인

- **타이포:** Paperlogy (한국어 geometric sans, Black + Medium)
- **팔레트:** Ink `#1a1a1a` on cream `#faf8f4` + deep forest `#2d4a3e` accent
- **레이아웃:** 720px editorial column + desktop 120px 좌측 이슈 레일
- **완전 접근성:** WCAG AA 대비, 키보드 네비, `:focus-visible`, skip link, reduced motion

자세한 내용은 `.gstack/projects/youtube-briefing/` 에 있는 디자인 문서 참조.

## 라이선스

Apache 2.0 — see [LICENSE](./LICENSE).
