# YouTube Briefing

_Auto-summarized Korean economics & current-affairs YouTube plus selected Naver blogs, as an editorial feed._

**Live site:** [kipeum86.github.io/youtube-briefing](https://kipeum86.github.io/youtube-briefing/)

**TL;DR (English).** A personal tool that watches 5 Korean YouTube channels
(박종훈, 슈카월드, 언더스탠딩, 지식인사이드, 지구본연구소) plus Mer's Naver
blog (메르의 네이버 블로그), extracts transcripts / blog post text,
generates 700–1,200-character Korean deep-analysis summaries with Gemini Flash,
and publishes them as a static Astro site on GitHub Pages. Updates Mon/Wed/Fri
at 06:00 KST via GitHub Actions. No database, no Sheets, no Google Cloud.
Fork-friendly: clone, add your Gemini API key, edit the source list, run the
pipeline.

## Forking this project

This repo is designed to be forked. Each user brings their own YouTube/blog
source list and their own Gemini API key. The live `config.yaml` is gitignored,
so clones of upstream see a clean template at `config.example.yaml`, and GitHub
Actions reads the real config from the `PIPELINE_CONFIG_YAML` repository secret.

To set up your own fork:

```bash
cp config.example.yaml config.yaml
```

Then edit `config.yaml` to list the sources you want to follow.
See `scripts/resolve-channel-ids.py` for converting YouTube `@handle` URLs to
channel IDs, and use `blogs:` for optional Naver blog sources. See the "Setup"
section below for secrets and deployment.

**Note on language:** The current version assumes Korean content and
Korean output. Multi-language support is planned but not yet shipped.
For now, forks should expect to read and modify the summarizer prompt
in `pipeline/summarizers/gemini_flash.py` if they want non-Korean output.

---

## 뭘 하는 건가

바쁠 때 경제·시사 유튜브와 블로그를 다 보기 어려워서 만든 개인용 브리핑 툴.
월·수·금 아침마다 다섯 개 유튜브 채널과 한 개 네이버 블로그의 새 콘텐츠를 자동 수집하고,
700–1,200자 한국어 심층 요약으로 정리해서 에디토리얼 피드로 보여준다.

- **타겟 소스:** 박종훈의 지식한방, 슈카월드, 언더스탠딩, 지식 인사이드, 지구본연구소, 메르의 네이버 블로그
- **유튜브 채널:** 박종훈의 지식한방, 슈카월드, 언더스탠딩, 지식 인사이드, 지구본연구소
- **네이버 블로그:** 메르의 네이버 블로그 (`https://blog.naver.com/ranto28`)
- **업데이트:** 주 3회 (Mon/Wed/Fri 06:00 KST, GitHub Actions cron)
- **스택:** Python 파이프라인 (GitHub Actions + NotebookLM session) + Astro 정적 사이트 (GitHub Pages)
- **저장소:** JSON 파일 in git (Google Sheets, DB 없음)

> **왜 NotebookLM 인가?** YouTube 는 클라우드 runner IP 를 `youtube-transcript-api`
> 와 `yt-dlp` 레벨에서 차단함. 유일하게 작동하는 경로는 로그인된 Google
> 세션을 통해 NotebookLM API 를 거치는 것. `NOTEBOOKLM_AUTH_JSON` GitHub
> secret 에 `~/.notebooklm/storage_state.json` 내용을 넣으면 GitHub runner
> 에서도 진짜 사용자처럼 요청이 나감. Gate 0 에서 실측 검증 완료.

## 설정 (7단계)

모든 실행은 GitHub Actions 에서. 네 Mac 은 **첫 세팅** 시에만 필요함 (채널 ID 조회, NotebookLM 로그인).

1. **포크 또는 클론**
   ```bash
   git clone https://github.com/YOUR_USERNAME/youtube-briefing
   cd youtube-briefing
   ```

2. **Gemini API 키 → GitHub secret**
   ```bash
   # 키 발급: https://aistudio.google.com/apikey (무료)
   gh secret set GEMINI_API_KEY -R YOUR_USERNAME/youtube-briefing
   # 프롬프트에 키 붙여넣기
   ```

3. **NotebookLM 로그인 (로컬에서 한 번만) → GitHub secret 업로드**
   ```bash
   pip install notebooklm-py
   notebooklm login
   # → 브라우저가 열림 → Google 로그인
   # → 세션이 ~/.notebooklm/storage_state.json 에 저장됨

   # 그 세션 파일을 secret 으로 업로드
   gh secret set NOTEBOOKLM_AUTH_JSON -R YOUR_USERNAME/youtube-briefing \
     < ~/.notebooklm/storage_state.json
   ```

   세션은 몇 주 단위로 만료. 실패하기 시작하면 위 두 줄 다시 실행.

4. **소스 설정 + config.yaml 수정** (로컬에서)
   ```bash
   brew install yt-dlp
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt

   python scripts/resolve-channel-ids.py https://www.youtube.com/@syukaworld
   # 출력: UCxxxxxxxxxxxxxxxx — 이걸 config.yaml 각 채널 id: 에 붙여넣기
   ```

   네이버 블로그를 같이 보고 싶다면 `blogs:` 아래에
   `blog_id`, `name`, `slug` 를 추가.

   ```yaml
   blogs:
     - blog_id: "ranto28"
       name: "메르의 블로그"
       slug: "mer"
   ```

   수정된 `config.yaml` 을 커밋 + 푸시.

5. **config.yaml 업로드 → GitHub secret**
   ```bash
   gh secret set PIPELINE_CONFIG_YAML -R YOUR_USERNAME/youtube-briefing < config.yaml
   ```

6. **GitHub Pages 활성화** — 저장소 Settings → Pages → Source: "GitHub Actions"

7. **첫 파이프라인 실행** — 수동 trigger 로 스모크 테스트
   ```bash
   # 1개 영상으로 먼저 검증
   gh workflow run pipeline -R YOUR_USERNAME/youtube-briefing \
     -f only_channel=shuka \
     -f limit=1
   gh run watch

   # 성공하면 전체 실행 (유튜브 5개 + 블로그 1개, 실행 시간은 소스 수에 따라 변동)
   gh workflow run pipeline -R YOUR_USERNAME/youtube-briefing
   ```

   끝. 이후엔 Mon/Wed/Fri 06:00 KST 에 자동 실행됨. 사이트 확인:
   `https://YOUR_USERNAME.github.io/youtube-briefing/`

## 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│  GitHub Actions                                              │
│                                                              │
│  .github/workflows/pipeline.yml                              │
│  (cron: Mon/Wed/Fri 06:00 KST, or manual dispatch)           │
│       │                                                      │
│       ▼                                                      │
│  pipeline/run.py                                             │
│       │                                                      │
│       ├─ fetchers/discovery.py  (YouTube RSS → yt-dlp        │
│       │                          catchup)                    │
│       ├─ fetchers/naver_blog.py (Naver RSS → mobile post)   │
│       │                                                      │
│       ├─ fetchers/transcript_extractor.py (3-tier)           │
│       │   ├─ tier 1: notebooklm-py (PRIMARY)                 │
│       │   │          reads NOTEBOOKLM_AUTH_JSON secret       │
│       │   │          = inlined storage_state.json            │
│       │   ├─ tier 2: youtube-transcript-api (IP-blocked      │
│       │   │          from runners, kept as local fallback)   │
│       │   └─ tier 3: yt-dlp VTT (same)                       │
│       │                                                      │
│       ├─ summarizers/gemini_flash.py                         │
│       │   (700-1,200 Korean chars, prompt v1)                │
│       │   reads GEMINI_API_KEY secret                        │
│       │                                                      │
│       └─ writers/json_store.py                               │
│              │                                                │
│              ▼                                                │
│       data/briefings/*.json                                  │
│              │                                                │
│       bot commits + pushes                                    │
│              │                                                │
│              │ (GITHUB_TOKEN push doesn't auto-trigger       │
│              │  downstream workflows → explicit dispatch)    │
│              ▼                                                │
│  .github/workflows/pages.yml                                 │
│       │                                                      │
│       ▼                                                      │
│  astro build → GitHub Pages deploy                           │
│       │                                                      │
│       ▼                                                      │
│  https://USERNAME.github.io/youtube-briefing/                │
└─────────────────────────────────────────────────────────────┘
```

### 트러블슈팅

- **`NOTEBOOKLM_AUTH_JSON secret is not set`** → step 3 다시
- **`NotebookLM session expired` 에러가 반복** → `notebooklm login` 재실행 + secret 재업로드
- **모든 tier 가 transient 실패** → YouTube 자체 문제, 다음 run 에서 자동 재시도
- **`GEMINI_API_KEY is not set`** → step 2 다시
- **파이프라인 로그** → `gh run view --log --job=JOB_ID`
- **수동 실행** (스케줄 안 기다리고):
  ```bash
  gh workflow run pipeline -R YOUR_USERNAME/youtube-briefing
  ```

### 로컬 개발 / 디버깅 (선택)

CI 가 기본이지만 로컬에서 직접 돌려볼 수 있음:

```bash
# 설정
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
npm install
brew install yt-dlp

cp .env.example .env
# .env 에 GEMINI_API_KEY 붙여넣기
notebooklm login  # ~/.notebooklm/storage_state.json 생성

# 로컬 실행
python pipeline/run.py --dry-run                        # 디스커버만
python pipeline/run.py --only-channel shuka --limit 1   # 스모크 테스트
python pipeline/run.py                                  # 전체
npm run dev                                             # localhost:4321 미리보기
```

원한다면 launchd 타이머도 설치 가능 (`scripts/install-launchd.sh`). 하지만 GitHub Actions 가 이미 같은 역할을 하니까 기본 설정에서는 불필요.

## 디자인

- **타이포:** Paperlogy (한국어 geometric sans, Black + Medium)
- **팔레트:** Ink `#1a1a1a` on cream `#faf8f4` + deep forest `#2d4a3e` accent
- **레이아웃:** 720px editorial column + desktop 120px 좌측 이슈 레일
- **완전 접근성:** WCAG AA 대비, 키보드 네비, `:focus-visible`, skip link, reduced motion

자세한 내용은 `.gstack/projects/youtube-briefing/` 에 있는 디자인 문서 참조.

## 라이선스

Apache 2.0 — see [LICENSE](./LICENSE).
