# 살짝 빗나간 추천 (chart-finder)

유튜브 시청 기록으로 관심사를 파악하고, **평소엔 접하지 않지만 관심사와 이어지는 "적당히 빗나간" 주제**를 하루 하나씩 추천하는 크롬 확장 프로그램입니다.

> (팝업 예시) 오늘의 주제: **음향학** — 위키백과 요약 · 위키백과/유튜브 링크 · 😄 재밌어요 / 🤔 별로예요

## 설치 (개발 모드)

1. 분석 모델 실행 라이브러리를 묶습니다 (처음 한 번, [Node.js](https://nodejs.org) 20 이상 필요):
   ```bash
   npm install --ignore-scripts
   npm run build        # extension/vendor/ 생성
   ```
2. `chrome://extensions` → 우측 상단 **개발자 모드** 켜기 → **압축해제된 확장 프로그램을 로드합니다** → `extension/` 폴더 선택
3. 로드하면 설정 페이지가 열리고, 백그라운드에서 **첫 준비**가 시작됩니다 (한 번만, 몇 분):
   - 위키백과에서 추천 주제 목록 받기
   - 분석 모델(약 120MB) 내려받기
   - 주제 수천 개를 모델로 분석해 저장
4. 선택 사항:
   - **지난 시청 기록 가져오기**: 크롬 방문 기록에서 최근 90일 `youtube.com/watch` 주소만 골라 가져와 바로 시작
   - **YouTube Data API 키**: 태그까지 분석에 써서 더 정확해짐 (없으면 제목만 사용)
5. 유튜브 영상이 5개 이상 쌓이면 툴바 아이콘 팝업에 오늘의 추천이 나타납니다.

```bash
npm run build                  # extension/vendor/ 다시 만들기
npm test                       # 단위 테스트
npm run test:e2e               # 실제 Chromium 에서 전체 흐름 (Playwright 필요, 외부 요청은 가짜 응답)
```

## 동작 방식

직접 만든 규칙 대신 **이미 있는 것**을 씁니다.

| 부분 | 쓰는 것 |
|---|---|
| 추천 후보 주제 | 영어 위키백과 [Vital articles Level 4](https://en.wikipedia.org/wiki/Wikipedia:Vital_articles/Level/4) — 위키백과 편집자들이 고른 핵심 문서 약 1만 개. 이 중 **한국어 문서가 있는 것**만 씀 (사람·장소 목록은 제외) |
| 주제 간 거리 | 사전학습 다국어 문장 임베딩 모델 [multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small)을 [Transformers.js](https://huggingface.co/docs/transformers.js)로 **브라우저 안에서** 실행 |

```
youtube.com/watch 15초 이상 시청 → 영상 ID + 제목 저장
  → 영상 제목(+태그)을 모델로 벡터화 → 비슷한 영상끼리 묶어 "관심 묶음" (최근일수록 비중 큼)
  → 주제 수천 개를 관심 묶음과 가까운 순으로 줄 세움
  → 상위 약 3% 근처에서 1개 선택 (너무 가까운 상위 0.5%는 이미 아는 분야라 피함)
  → 팝업: 주제 + 위키백과 요약 + 위키백과/유튜브 링크 + 반응 버튼
```

- **거리는 순위로 잽니다.** 유사도 숫자는 모델마다 범위가 달라서, "가까운 순으로 상위 몇 %"를 목표로 삼습니다.
- **반응**: 재밌어요 → 목표를 30% 더 멀리 / 별로예요 → 30% 더 가까이 (0.3%~30% 사이). 별로예요 받은 주제와 거의 같은 주제는 점수가 깎입니다.
- 최근 90일 안에 추천한 주제, 별로예요 받은 주제는 다시 나오지 않습니다.
- 서비스 워커가 중간에 꺼져도 준비 작업은 저장된 지점부터 이어서 합니다.

## 개인정보 & 웹스토어 정책

자세한 내용은 [PRIVACY.md](PRIVACY.md)를 보세요. 요약하면:

- **권한**: `storage`, `unlimitedStorage`(모델 캐시), `alarms`, `youtube.com` 콘텐츠 스크립트, 그리고 YouTube API·위키백과·Hugging Face 호출용 호스트 권한. 전체 방문 기록(`history`)은 **선택 권한**으로, "가져오기" 버튼을 누를 때만 요청하고 끝나면 즉시 반납합니다.
- **로컬 처리**: 시청 기록은 브라우저에만 저장되고, 분석 모델도 브라우저 안에서 돕니다. 위키백과·Hugging Face에는 주제 목록과 모델 파일을 **받기만** 하고 시청 기록은 보내지 않습니다. 개발자 서버가 없습니다.
- **최소 수집**: 15초 이상 머문 영상의 ID·제목(+API 메타데이터)만 저장, 180일/1,000개가 지나면 자동 삭제.
- 시크릿 모드 미지원(`"incognito": "not_allowed"`), 수집 일시정지, 데이터 전체 삭제 제공.

## 구조

```
extension/
  manifest.json
  vendor/                Transformers.js + ONNX Runtime wasm 번들 (npm run build 로 생성, git 에는 없음)
  src/
    background.js        서비스 워커: 메시지 라우팅, 알람, 기록 가져오기, 준비 작업 재개
    content.js           youtube.com 시청 감지 (15초 체류 후 ID+제목 전송)
    lib/
      wiki.js            Vital articles 목록, 한국어 문서 연결, 설명·요약 조회
      embedder.js        multilingual-e5-small 실행 (Transformers.js)
      engine.js          주제 목록 만들기(이어하기 지원), 영상 벡터 캐시
      interest-map.js    가중 k-means 관심 묶음
      recommender.js     순위 기반 "살짝 빗나간" 선택, 반응 반영
      service.js         하루 추천 한도/상태 관리 (chrome 비의존, 테스트 가능)
      store.js           chrome.storage.local 래퍼 (쓰기 직렬화, 보관 기간)
      idb.js             IndexedDB (주제 벡터 저장)
      youtube.js         URL 파싱, Data API 조회
  popup/                 오늘의 추천 + 반응 버튼
  options/               설정, 주제 목록 상태, 관심 묶음, 기록 가져오기, 데이터 삭제
tests/                   단위 테스트 + e2e/ (가짜 모델·위키백과로 실제 Chromium 실행)
```

## 다음 단계

- **실제 모델·위키백과로 추천 품질 확인**: 목표 순위 기본값(상위 3%)은 가짜 모델로만 검증했습니다. 실제로 써 보면서 조정이 필요합니다.
- **수집 범위 확대**: 뉴스·블로그 등 지정 사이트.
- **모바일**: 반응 기반 추천으로 보완.
