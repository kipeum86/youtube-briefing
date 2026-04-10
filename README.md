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
- **업데이트:** 주 3회 (Mon/Wed/Fri 06:00 KST, GitHub Actions cron)
- **스택:** Python 파이프라인 (GitHub Actions) + Astro 정적 사이트 (GitHub Pages)
- **저장소:** JSON 파일 in git (Google Sheets, DB 없음)

## 설정 (5단계)

클라우드 (GitHub Actions) 에서 자동으로 돌아가는 모드가 디폴트. 로컬 설치는 개발/디버깅 용.

1. **포크 또는 클론**
   ```bash
   git clone https://github.com/YOUR_USERNAME/youtube-briefing
   cd youtube-briefing
   ```

2. **Gemini API 키 발급 + GitHub Secret 등록**
   ```bash
   # 키 발급: https://aistudio.google.com/apikey (무료)
   gh secret set GEMINI_API_KEY -R YOUR_USERNAME/youtube-briefing
   # 프롬프트에 키 붙여넣기
   ```

3. **채널 ID 조회 + config.yaml 수정** — 로컬에서 한 번만
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   brew install yt-dlp  # 또는 pipx install yt-dlp

   python scripts/resolve-channel-ids.py https://www.youtube.com/@syukaworld
   # 출력: UCxxxxxxxxxxxxxxxx — 이걸 config.yaml 의 각 채널 id: 에 붙여넣기
   ```
   수정된 `config.yaml` 을 커밋 + 푸시.

4. **GitHub Pages 활성화** — 저장소 Settings → Pages → Source: "GitHub Actions"

5. **첫 파이프라인 실행** — 수동 트리거로 검증
   ```bash
   gh workflow run pipeline -R YOUR_USERNAME/youtube-briefing
   gh run watch
   ```

   끝. 이후엔 Mon/Wed/Fri 06:00 KST 에 자동 실행됨. 새 briefing 생기면
   자동으로 커밋 + 푸시 + Pages 재배포. 사이트에서 확인:
   `https://YOUR_USERNAME.github.io/youtube-briefing/`

### (선택) NotebookLM 폴백 — 봇 차단 대비

`youtube-transcript-api` 와 `yt-dlp` 두 레이어로 거의 모든 영상을 커버하지만,
만약 YouTube 가 봇 차단을 강화해서 두 레이어 모두 실패하기 시작하면 NotebookLM 을
3차 폴백으로 활성화 가능:

```bash
# 로컬에서 NotebookLM 세션을 Playwright storage_state.json 으로 export
# (자세한 절차는 notebooklm-py 문서 참조)
gh secret set NOTEBOOKLM_AUTH_JSON < storage_state.json \
  -R YOUR_USERNAME/youtube-briefing
```

파이프라인 코드는 변경 없음 — `NOTEBOOKLM_AUTH_JSON` 환경변수가 설정되면
자동으로 2차 폴백이 활성화됨. 세션 만료 시 주기적으로 재등록 필요.

## 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│  GitHub Actions                                              │
│                                                              │
│  .github/workflows/pipeline.yml                              │
│  (cron: Mon/Wed/Fri 06:00 KST or manual dispatch)            │
│       │                                                      │
│       ▼                                                      │
│  pipeline/run.py                                             │
│       │                                                      │
│       ├─ fetchers/discovery.py  (YouTube RSS → yt-dlp        │
│       │                          catchup)                    │
│       │                                                      │
│       ├─ fetchers/transcript_extractor.py                    │
│       │   ├─ tier 1: youtube-transcript-api (primary)        │
│       │   ├─ tier 2: notebooklm-py (optional, if secret set) │
│       │   └─ tier 3: yt-dlp VTT (last resort)                │
│       │                                                      │
│       ├─ summarizers/gemini_flash.py                         │
│       │   (needs GEMINI_API_KEY secret)                      │
│       │                                                      │
│       └─ writers/json_store.py                               │
│              │                                                │
│              ▼                                                │
│       data/briefings/*.json  → git commit + push             │
│                                                               │
│              │                                                │
│              │  workflow-triggered dispatch                   │
│              ▼                                                │
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

### 로컬 개발 / 디버깅

프로덕션은 GitHub Actions 에서 돌지만 로컬에서 직접 실행도 가능:

```bash
# 한 번 셋업
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
npm install
brew install yt-dlp

cp .env.example .env
# .env 에 GEMINI_API_KEY 붙여넣기

# 로컬 실행
python pipeline/run.py --dry-run   # 검색만, 호출 없음
python pipeline/run.py              # 실제 실행 + data/briefings/ 에 쓰기
npm run dev                          # localhost:4321 에서 미리보기
```

## 디자인

- **타이포:** Paperlogy (한국어 geometric sans, Black + Medium)
- **팔레트:** Ink `#1a1a1a` on cream `#faf8f4` + deep forest `#2d4a3e` accent
- **레이아웃:** 720px editorial column + desktop 120px 좌측 이슈 레일
- **완전 접근성:** WCAG AA 대비, 키보드 네비, `:focus-visible`, skip link, reduced motion

자세한 내용은 `.gstack/projects/youtube-briefing/` 에 있는 디자인 문서 참조.

## 라이선스

Apache 2.0 — see [LICENSE](./LICENSE).
