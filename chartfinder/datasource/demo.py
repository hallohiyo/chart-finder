"""오프라인 데모용 합성 데이터 소스.

네트워크 없이 CLI/웹 UI/스크리너를 끝까지 시험해보기 위한 것. 실제 시세가 아니다.
시드가 고정돼 있어 같은 종목은 항상 같은 차트를 만든다.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from .base import DataSource, Ticker, normalize_ohlcv

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

    def __init__(self, count: int = 60) -> None:
        self.count = count

    def list_tickers(self, universe: str = "all") -> list[Ticker]:
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


def generate(symbol: str, start: date, end: date) -> pd.DataFrame:
    """종목코드를 시드로 한 결정적 랜덤워크 일봉."""
    index = pd.bdate_range(start, end)
    n = len(index)
    if n < 2:
        raise ValueError("기간이 너무 짧습니다.")

    seed = abs(hash(symbol)) % (2**32)
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

    return pd.DataFrame(
        {
            "open": open_, "high": high, "low": low, "close": close,
            "volume": volume, "value": volume * close,
        },
        index=index,
    )
