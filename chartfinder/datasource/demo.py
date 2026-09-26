"""오프라인 데모용 합성 데이터 소스.

네트워크 없이 CLI/웹 UI/스크리너를 끝까지 시험해보기 위한 것. 실제 시세가 아니다.
시드가 고정돼 있어 같은 종목은 항상 같은 차트를 만든다.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from .base import FLOW_COLUMNS, DataSource, Ticker, normalize_ohlcv

#: 종목마다 다른 성격의 차트가 나오도록 섞어둔 프로필
_PROFILES = [
    ("상승추세", 0.0012, 0.018),
    ("급등", 0.0035, 0.035),
    ("횡보", 0.0000, 0.010),
    ("하락추세", -0.0012, 0.020),
    ("급락후반등", -0.0005, 0.030),
    ("저변동", 0.0004, 0.007),
]


class DemoSource(DataSource):
    market = "demo"
    universes = ("all",)
    supports_flows = True  # 합성 수급 데이터가 일봉에 함께 들어 있다
    supports_fundamentals = True
    supports_profiles = True

    def __init__(self, count: int = 60) -> None:
        self.count = count

    def list_tickers(self, universe: str = "all") -> list[Ticker]:
        if universe.lower() not in self.universes:
            raise ValueError(f"지원하지 않는 유니버스: {universe} (가능: {self.universes})")
        tickers = []
        for i in range(self.count):
            profile = _PROFILES[i % len(_PROFILES)][0]
            tickers.append(
                Ticker(
                    symbol=f"DEMO{i:03d}",
                    name=f"데모{i:03d} ({profile})",
                    market=self.market,
                    exchange="DEMO",
                )
            )
        return tickers

    def fetch_ohlcv(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        return normalize_ohlcv(generate(symbol, start, end))

    def fetch_flows(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        df = generate(symbol, start, end)
        return df[[c for c in FLOW_COLUMNS if c in df.columns]]

    def fetch_fundamentals(self, symbol: str) -> pd.DataFrame:
        return generate_fundamentals(symbol)

    def fetch_profiles(self, symbols: list[str], universe: str = "all") -> pd.DataFrame:
        self.profile_notes = {"합성 종목 정보": "받음"}
        return generate_profiles(symbols)


def _seed(symbol: str) -> int:
    """종목코드를 난수 시드로. hash() 는 실행마다 솔트가 바뀌어 쓸 수 없다.

    파이썬은 프로세스마다 문자열 해시를 다르게 매기므로, hash() 로 시드를 만들면
    같은 종목이 실행할 때마다 다른 데이터가 되어 시험 결과가 들쭉날쭉해진다.
    """
    import zlib

    return zlib.crc32(symbol.encode())


def generate(symbol: str, start: date, end: date) -> pd.DataFrame:
    """종목코드를 시드로 한 결정적 랜덤워크 일봉."""
    index = pd.bdate_range(start, end)
    n = len(index)
    if n < 2:
        raise ValueError("기간이 너무 짧습니다.")

    seed = _seed(symbol)
    rng = np.random.default_rng(seed)
    _, drift, vol = _PROFILES[seed % len(_PROFILES)]

    # 추세 + 완만한 사이클을 섞어 이평 교차·박스권 같은 패턴이 실제로 나오게 한다
    cycle = np.sin(np.linspace(0, rng.uniform(2, 6) * np.pi, n)) * vol * 3
    steps = rng.normal(drift, vol, n) + np.diff(cycle, prepend=cycle[0])
    close = float(rng.integers(5_000, 200_000)) * np.exp(np.cumsum(steps))

    intraday = np.abs(rng.normal(0, vol / 2, n))
    open_ = close * (1 + rng.normal(0, vol / 3, n))
    high = np.maximum(open_, close) * (1 + intraday)
    low = np.minimum(open_, close) * (1 - intraday)
    volume = rng.lognormal(mean=12, sigma=0.6, size=n).round()
    # 거래량 급증 이벤트 몇 번 심어두기
    spikes = rng.choice(n, size=max(1, n // 60), replace=False)
    volume[spikes] *= rng.uniform(3, 8, size=len(spikes))

    # 수급(외국인·기관 순매수)도 흉내낸다. 주가 등락과 약하게 연동시켜
    # '수급이 붙으면 오른다' 정도의 관계가 보이게 한다.
    drift_signal = np.concatenate([[0.0], np.diff(np.log(close))])
    foreign = (drift_signal * volume * rng.uniform(0.2, 0.5)
               + rng.normal(0, volume.mean() * 0.02, n)).round()
    inst = (drift_signal * volume * rng.uniform(0.1, 0.4)
            + rng.normal(0, volume.mean() * 0.02, n)).round()

    return pd.DataFrame(
        {
            "open": open_, "high": high, "low": low, "close": close,
            "volume": volume, "value": volume * close,
            "foreign_net": foreign, "inst_net": inst, "indi_net": -(foreign + inst),
        },
        index=index,
    )


def generate_fundamentals(symbol: str, years: int = 5) -> pd.DataFrame:
    """종목코드를 시드로 한 합성 재무제표. 성장·정체·역성장이 섞이게 만든다."""
    from ..fundamentals import normalize

    seed = _seed(symbol)
    rng = np.random.default_rng(seed + 7)
    growth = rng.uniform(-0.15, 0.35)  # 연평균 매출 성장률
    margin = rng.uniform(-0.05, 0.25)  # 영업이익률

    end_year = date.today().year - 1
    periods = list(range(end_year - years + 1, end_year + 1))
    base = float(rng.integers(50_000, 5_000_000))

    revenue = base * np.cumprod(1 + rng.normal(growth, 0.08, years))
    operating = revenue * np.clip(margin + rng.normal(0, 0.03, years), -0.5, 0.6)
    net = operating * rng.uniform(0.5, 0.9)
    equity = revenue * rng.uniform(0.4, 1.2)

    return normalize(
        pd.DataFrame(
            {
                "revenue": revenue,
                "operating_income": operating,
                "net_income": net,
                "debt_ratio": np.clip(rng.normal(rng.uniform(30, 180), 15, years), 5, 500),
                "roe": net / equity * 100.0,
                # 일부 종목은 자본잠식(유보율 음수) 상태로 만들어 위험 조건을 시험한다
                "reserve_ratio": np.clip(
                    rng.normal(rng.uniform(-80, 900), 60, years), -200, 5000
                ),
                "operating_cash_flow": operating * rng.uniform(0.6, 1.4, years),
            },
            index=periods,
        )
    )


def generate_profiles(symbols: list[str]) -> pd.DataFrame:
    """합성 종목 정보. 실제 출처가 없어도 종목정보 조건을 시험할 수 있게."""
    rows = {}
    for symbol in symbols:
        rng = np.random.default_rng(_seed(symbol) + 13)
        shares = float(rng.integers(3_000_000, 500_000_000))
        major = float(rng.uniform(5.0, 75.0))
        rows[symbol] = {
            "marcap": shares * float(rng.integers(1_000, 200_000)),
            "shares": shares,
            "float_shares": shares * (1.0 - major / 100.0),
            "major_pct": major,
            "foreign_pct": float(rng.uniform(0.0, 40.0)),
            "short_ratio": float(rng.uniform(0.0, 12.0)),
            "loan_ratio": float(rng.uniform(0.0, 8.0)),
            "dilution_pct": float(rng.choice([0.0, 0.0, rng.uniform(1.0, 25.0)])),
        }
    return pd.DataFrame.from_dict(rows, orient="index")
