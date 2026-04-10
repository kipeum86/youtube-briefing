# YouTube Briefing — 브레인스토밍 대화 기록

**날짜**: 2026-04-09
**참여자**: KP, Claude (Opus 4.6)
**목적**: 유튜브 시사/경제 콘텐츠 자동 요약 & 웹 대시보드 프로젝트 설계

---

## 초기 아이디어 (KP)

> 좋은 프로젝트 아이디어가 생각났음.
>
> podcast-briefing 과 비슷한데, YouTube 버전이야. 내가 평소 시사/경제 관련 유튜브 콘텐츠를 많이 보는데, 요새는 통 시간이 없어서 볼 시간이 없거든? 내가 자주 보는 채널 몇개 선정해서, 이걸 깃허브 액션으로 특정 주기마다 내용을 가져와서 (Notebook-py 활용), 내용을 요약 + 정리하고, 구글 시트에 내용 채우고 (컨트롤 패널 역할), 웹 페이지로 보여주는 거야
>
> Podcast-briefing 또는 Parlawatch 웹페이지 스타일 정도 생각하는데..아직 뭐가 더 나은진 모르겠네? 채널이 여러개니까 후자가 나을 거 같기도 하고.. 아카이브 페이지도 있어야 하고, 한국어로만 요약하면 되긴 하는데.. 링크드인에 자랑해야 해서..한/영 토글도 추가하고.. ㅎㅎ what do you think?

---

## 기존 프로젝트 컨텍스트 탐색 결과

### podcast-briefing
- **위치**: `/Users/kpsfamily/코딩 프로젝트/podcast-briefing`
- **아키텍처**: GitHub Actions 기반 자동 파이프라인 (월/목 06:00 UTC)
- **스택**: Astro (정적 사이트) + Python 파이프라인 + GitHub Pages
- **수집**: 10개 팟캐스트 RSS → Whisper API 트랜스크립션 → Claude Sonnet 요약
- **요약 스타일**: "Economist 스타일" (thesis → evidence → implications)
- **웹 구조**: 720px 에디토리얼 1칼럼 (#fafaf8 bg, #b44 red, Georgia serif)
- **페이지**: `index.astro` (최근 7일) + `archive.astro` (주차별)
- **한/영 토글**: CSS display swap + localStorage
- **Google Sheets**: 데이터 저장소 역할 (Date, Podcast, Title, Guest, Category, ⭐ rating, ✔읽음, Transcript, 양방향 요약 등)

### ParlaWatch
- **위치**: `/Users/kpsfamily/코딩 프로젝트/parlawatch`
- **아키텍처**: Vanilla JS SPA + GitHub Pages + GitHub Actions
- **수집**: YouTube NATV → 자막 추출(youtube-transcript-api → NotebookLM → yt-dlp fallback) → Claude 2-pass 분석
- **저장**: Google Sheets 6탭 (_processed_videos, _manual_queue, _keywords, agendas, statements, news_articles)
- **웹 구조**: 다크 테마 대시보드, 헤더 + 필터 바 + 통계 + 카드 + 모달
- **디자인**: CSS 변수 기반 (primary: #2563eb), 컴포넌트 스타일
- **i18n**: 현재 한국어 전용 (README만 한/영)

### auto-newsbriefing
- **위치**: `/Users/kpsfamily/코딩 프로젝트/auto-newsbriefing`
- **아키텍처**: CLI 전용 6단계 파이프라인 (웹 UI 없음)
- **수집**: RSS feedparser → BeautifulSoup → Claude/OpenAI/Gemini 요약
- **출력**: Google Sheets 저장 + SMTP HTML 이메일
- **스케줄**: Mon/Wed/Fri cron

---

## Q&A 대화 기록

### Q1: 타겟 유튜브 채널

**Claude**: 어떤 유튜브 채널들을 타겟으로 하고 있어요?

**KP**: 5개 채널 확정됨:
- **박종훈의 지식한방**: https://www.youtube.com/@kpunch
- **슈카월드**: https://www.youtube.com/@syukaworld
- **언더스탠딩**: https://www.youtube.com/@understanding
- **지식 인사이드**: https://www.youtube.com/@지식인사이드
- **최준영 박사의 지구본 연구소**: https://www.youtube.com/@globelab

### Q2: 업데이트 주기

**Claude**: 매일? 주 2~3회? 주 1회? 범위는?

**KP**: **주 3회** 정도가 좋을 듯.

### Q3: 콘텐츠 수집 방식

**Claude**: A) YouTube 자막 추출 vs B) 오디오 → Whisper?

**KP**: 경험상 유튜브에서 봇 접근 다 막아놔서, fallback 옵션인 **NotebookLM-py로만 수집 가능**. → NotebookLM-py 기반 확정.

### Q4: 요약 스타일

**Claude**: A) 심층 분석 vs B) 가벼운 뉴스 브리핑 vs C) 채널별 차등?

**KP**: **심층 분석 + 채널간 구분 필요 없음**.

### Q5: 궁극적 목표

**KP**: 궁극적으로 유튜브 영상 실제로 안보고 주요 내용 글로 읽고 싶은 것. 그러다 정말 관심있는 내용 생기면 영상 실제로 보면 됨.

**Claude 해석**: 요약이 "영상 볼만한지 판단하는 필터" + "영상 대체 읽을거리" 역할. 영상 링크도 잘 띄게 배치 필요.

### Q6: 분량

**Claude**: A) 1000~1500자 vs B) 2000~3000자 vs C) 3000자+?

**KP**: 너무 길면 읽기 싫어질 수 있어서 **500~1,000자** 정도.

→ 짧은 만큼 정보 밀도가 핵심. "영상의 고갱이만 뽑은 핵심 테이크어웨이" 느낌.

### Q7: 카테고리 분류

**Claude**: A) 고정 카테고리 vs B) 채널별만 vs C) 둘 다?

**KP**: **별도 카테고리 구분 없이 채널별 구분만**.

### Q8: 레이아웃 방향

**Claude**: A) 에디토리얼 피드 (podcast-briefing 스타일) vs B) 대시보드 그리드 (ParlaWatch 스타일)?
→ 브라우저 비주얼 컴패니언으로 목업 제시 (youtube-briefing-layout-comparison.html 참조)

**KP**: **A. 에디토리얼 스타일로 가되, 상단에 채널 필터 칩 추가** (all + 각 채널 버튼).

### Q9: 구글 시트 역할

**Claude**: A) 데이터 저장소 vs B) 설정 컨트롤 vs C) 둘 다?

**KP**: 구글시트가 없는 게 다른 이용자들이 풀 받아서 쓰기 더 편할 것 같은데 어떻게 생각해?

**Claude 제안**:
> 같은 생각이에요. 시트 없애는 게 낫다고 봐요.
>
> **포크 가능성 관점**: 시트 쓰면 서비스 계정 JSON + 시트 ID 세팅 필요 → 진입 장벽. 링크드인에서 본 사람이 바로 git clone && 수정 하기 어려움. 기존 프로젝트 3개(podcast-briefing, parlawatch, auto-newsbriefing) 다 시트 기반이라 이 프로젝트는 "시트 없이도 된다"는 좋은 레퍼런스.
>
> **대안 구조**:
> - 채널 설정 → `config.yaml`
> - 데이터 저장 → `data/briefings/*.json` (깃에 커밋)
> - 개인 기능(읽음/평점/메모) → 브라우저 localStorage
> - GitHub Actions → 주 3회 실행 후 auto-commit → GitHub Pages 재배포

**→ 최종 결정 필요 (아직 미확정)**

---

## 현재까지 확정된 사양

| 항목 | 결정 |
|---|---|
| **타겟 채널** | 박종훈의 지식한방, 슈카월드, 언더스탠딩, 지식 인사이드, 최준영 박사의 지구본 연구소 (5개) |
| **업데이트 주기** | 주 3회 (GitHub Actions 스케줄) |
| **수집 방식** | NotebookLM-py 기반 트랜스크립트 추출 |
| **요약 스타일** | 심층 분석, 채널간 구분 없음 |
| **요약 분량** | 500~1,000자 (핵심 테이크어웨이, 정보 밀도 중시) |
| **궁극 목적** | 영상 안 보고 글로 대체 + 관심 가는 것만 영상 시청 |
| **분류** | 채널별 구분만 (별도 카테고리 없음) |
| **웹 레이아웃** | 에디토리얼 1칼럼 + 상단 채널 필터 칩 (All, 슈카월드, ...) |
| **아카이브 페이지** | 필요 |
| **한/영 토글** | 필요 (링크드인 공유용) |
| **데이터 저장** | **미확정** — Google Sheets 제거 후 JSON + localStorage 방향 제안됨 |

---

## 남은 결정 사항

1. **데이터 저장 방식 최종 확정** — Sheets 제거 + JSON/localStorage 방향 확정할지
2. **번역 방식** — 한국어 1차 요약 후 영어 번역 (2단계 LLM) vs 동시 생성
3. **상세 페이지 구조** — 피드에서 펼쳐보기 vs 별도 페이지 vs 모달
4. **아카이브 구조** — 날짜별/주차별/채널별 그룹핑
5. **원본 트랜스크립트** — 사이트에 같이 제공할지 (토글로 펼쳐보기 등)
6. **디자인 디테일** — 컬러(빨강 podcast-briefing 스타일 유지? 변경?), 타이포그래피
7. **빈 상태 처리** — 처음 사용자가 clone해서 데이터 없을 때 안내

---

## 참고 파일

- 레이아웃 비교 목업: `./layout-comparison.html`
- 기존 레퍼런스: `/Users/kpsfamily/코딩 프로젝트/podcast-briefing/`
- 기존 레퍼런스: `/Users/kpsfamily/코딩 프로젝트/parlawatch/`
- 기존 레퍼런스: `/Users/kpsfamily/코딩 프로젝트/auto-newsbriefing/`

---

## 다음 단계

남은 결정 사항을 마저 정리한 후, 디자인 문서(spec)를 `docs/superpowers/specs/2026-04-09-youtube-briefing-design.md`에 작성하고, writing-plans skill로 구현 계획을 만들어 진행.
