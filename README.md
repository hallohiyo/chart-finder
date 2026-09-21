# chart-finder

원하는 차트 조건을 자유롭게 조합해서, **그 조건에 가장 근접한 종목**을 뽑아주는 스크리너.
한국(KOSPI/KOSDAQ)과 미국(NYSE/NASDAQ) 시장을 모두 지원하고, CLI와 웹 UI 두 가지로 쓸 수 있다.

## 뭐가 다른가: 근접도 점수

일반 스크리너는 조건을 참/거짓으로 잘라서 하나라도 못 맞추면 결과에서 사라진다.
여기서는 조건마다 **0~1 점수**를 매기고 가중 평균으로 랭킹한다.

```
RSI(14) < 30 조건에서
  RSI 25 → 1.00 (충족)
  RSI 31 → 0.96 (아깝게 미달, 여전히 후보)
  RSI 45 → 0.02
```

덕분에 "5개 조건 중 4.5개를 만족하는 종목"이 자연스럽게 상위에 올라온다.
기존 방식이 필요하면 `--strict` (웹 UI에서는 '엄격 모드')를 켜면 된다.

## 설치

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[ui,dev]"
```

## 사용법 (CLI)

```bash
# 1. 조건 목록 보기
chartfinder conditions
chartfinder conditions -k rsi_oversold      # 조건 하나의 파라미터 상세

# 2. 일봉 데이터 받기 (최초 1회, 이후엔 증분 갱신)
chartfinder update -m kr -u all -y 3
chartfinder update -m us -u sp500 -y 3

# 3. 검색
chartfinder scan -m kr \
  -c ma_alignment \
  -c "ma_pullback:period=20,max_gap=3,weight=2" \
  -c "volume_dryup:ratio=0.7" \
  --top 20 --detail

# 프리셋으로 검색 + CSV 저장
chartfinder scan -p presets/pullback_buy.yaml --top 30 --csv out/result.csv

# 캐시 상태
chartfinder status -m kr
```

조건 문법은 `키:파라미터=값,파라미터=값,weight=가중치`. 파라미터를 생략하면 기본값을 쓴다.

## 사용법 (데스크톱 창)

파이썬 기본 내장 tkinter로 만든 창. 별도 설치나 브라우저 없이 바로 뜬다.

```bash
chartfinder-ui
# 또는
python -m chartfinder.ui
```

- 위쪽에서 시장/유니버스를 고르고 **데이터 받기** → 끝나면 **검색**
- 왼쪽 조건을 체크하면 파라미터 입력칸이 펼쳐진다 (가중치도 여기서)
- 결과 행을 **더블클릭**하면 캔들차트가 브라우저에 열린다
- 프리셋 불러오기, CSV 저장 지원
- 수집·검색은 별도 스레드에서 돌아가므로 창이 멈추지 않는다

## 사용법 (웹 UI)

```bash
streamlit run chartfinder/app.py
```

- 왼쪽에서 시장/유니버스를 고르고 **데이터 받기**로 캐시를 채운다
- 가운데 카테고리를 펼쳐 조건을 **체크**하면 파라미터 입력칸과 가중치 슬라이더가 나타난다
- **검색** → 점수순 결과표 → 행을 클릭하면 캔들차트(MA20/60/120 + 거래량)가 뜬다
- 조건 조합은 프리셋으로 저장/불러오기 가능

## 네트워크 없이 먼저 써보기

`demo` 시장은 합성 데이터를 만들어 쓴다. 실제 시세가 아니지만 전체 흐름을 그대로 시험해볼 수 있다.

```bash
chartfinder update -m demo
chartfinder scan -m demo -c ma_alignment -c volume_surge --top 10 --detail
```

## 내장 조건 (42개)

| 카테고리 | 조건 |
|---|---|
| 추세 | 이동평균 정배열, 골든크로스, 이동평균 위, 이동평균 상승 추세, 이평 눌림목, DMI +DI 상향 돌파, DMI 간격 확대, 이동평균 상승 전환, 이동평균 수렴 |
| 모멘텀 | RSI 과매도/과매수/구간, RSI 기준선 상향 돌파, MACD 골든크로스(강세권/약세권 구분), MACD 히스토그램 상승 전환(초입 매수신호)/하락 전환, MACD 0선 위, 스토캐스틱 과매도, 스토캐스틱 골든크로스, 기간 수익률 구간, 연속 상승 |
| 변동성 | 볼린저 상단 돌파/하단 이탈/스퀴즈, 볼린저 하단 이탈 후 복귀, ATR 구간 |
| 거래량 | 거래량 급증, 거래량 감소, 상승 캔들 + 거래량 증가 |
| 수급 | 외국인 연속 순매수, 기관 연속 순매수, 누적 순매수 수량 |
| 가격위치 | 신고가 근접, 신저가 근접, 고점 대비 낙폭 구간, 특정 가격 근접 |
| 패턴 | 박스권 횡보, N일 신고가 돌파, 조정 후 반등, 갭 상승 |
| 필터 | 최소 거래대금, 주가 구간 |

### 외국인·기관 수급

수급 조건은 일봉과 별도로 투자자별 순매수를 받아야 쓸 수 있다. **한국 시장 전용**이고,
종목당 요청이 1회 늘어나므로 수집이 느려진다.

```bash
chartfinder update -m kr -u all --flows
```

수급 수집에는 `pykrx` 가 필요하다 (의존성에 포함돼 있다). 0건으로 끝나면
실패 사유가 함께 출력된다.

수집 경로는 두 단계다. **KRX 일별추이(pykrx)** 를 먼저 시도하고, 빈 응답이면
**네이버 금융** 표를 읽는다. KRX 쪽은 차단이나 엔드포인트 변경으로 빈 응답을
주는 일이 잦다. 처음 받을 때는 최근 120일치만 받는다 (수급 조건이 보는 구간은
길어야 수십 일이다).

## 수집이 안 될 때

어디서 막히는지 단계별로 확인한다.

```bash
chartfinder doctor -m kr
chartfinder doctor -m kr --symbol 005930 --days 30
```

종목 목록 → 시세 → 수급 원본 응답 → 정규화 순으로 성공/실패와 실제 응답 형태를 보여준다.

UI에서는 '수급 포함' 체크박스. 받지 않은 상태로 수급 조건을 쓰면 점수가 0으로 나온다.

### 조건 추가하기

`chartfinder/conditions/builtin.py` 에 함수 하나만 추가하면 CLI 옵션과 웹 UI 위젯이 자동으로 생긴다.

```python
@condition(
    "my_rule", "내 조건", TREND,
    params=(_p("period", "기간", default=20, min=5, max=120),),
    description="설명",
)
def my_rule(ctx: Ctx, period: int) -> float:
    return soft_gt(ctx.last(ctx.close), ctx.last(ctx.ma(period)))
```

`ctx` 는 해당 종목의 일봉과 지표 캐시를 들고 있고, 반환값은 0~1 점수다.

## 프리셋

| 파일 | 내용 |
|---|---|
| `bottom_reversal.yaml` | 바닥권 반등 19종 (볼린저·RSI·수급·DMI·스토캐스틱·이평·MACD·거래량). `--flows` 필요 |
| `bottom_reversal_noflow.yaml` | 위에서 수급만 제외. 미국 시장이나 수급 미수집 시 |
| `pullback_buy.yaml` | 정배열 상승 추세 중 20일선 눌림목 |
| `oversold_rebound.yaml` | 과매도 + 볼린저 하단 이탈 바닥권 |
| `breakout.yaml` | 박스권을 거래량 동반 돌파 |
| `near_52w_high.yaml` | 52주 신고가 근접 모멘텀 |

```bash
chartfinder scan -p presets/bottom_reversal.yaml --top 30 --detail
```

## 구조

```
chartfinder/
  datasource/    시장별 어댑터 (krx / us / demo) — 공통 OHLCV 스키마로 정규화
  cache.py       parquet 로컬 캐시 + 증분 갱신
  indicators.py  기술적 지표 (pandas 벡터 연산)
  scoring.py     근접도 점수 함수 (soft_lt / soft_gt / ordered ...)
  conditions/    조건 레지스트리 — 파라미터 스키마가 UI/CLI를 자동 생성
  screener.py    전 종목 채점 + 가중 랭킹
  charts.py      캔들차트 (두 UI가 공유)
  cli.py         커맨드라인
  ui.py          tkinter 데스크톱 창
  app.py         Streamlit 웹 UI
presets/         조건 세트 YAML
```

캐시 위치는 기본 `~/.chartfinder` 이고 `CHARTFINDER_HOME` 환경변수로 바꿀 수 있다.

## 테스트

```bash
pytest
```

UI 테스트는 tkinter나 디스플레이가 없는 환경에서는 자동으로 건너뛴다.

## 알아둘 점

- **일봉 기준**이고 실시간이 아니다. 장중 데이터는 반영이 늦거나 빠질 수 있다.
- 데이터 출처는 무료 API(FinanceDataReader, yfinance)라 수정주가·결측 처리는 출처에 의존한다.
- 미국 전 종목(`-u all`) 최초 수집은 수십 분이 걸린다. `sp500` 으로 시작하는 걸 권한다.
- 투자 판단 도구가 아니라 **후보 탐색 도구**다. 결과는 반드시 직접 검증할 것.
