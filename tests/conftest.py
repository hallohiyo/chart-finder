import os

# rich 는 import 시점에 콘솔 폭을 잡는다. 터미널이 좁으면 한글 라벨이 잘려
# CLI 출력 검사가 실행 환경에 따라 달라지므로, 테스트에서는 폭을 고정한다.
# (테스트 모듈보다 conftest 가 먼저 import 된다)
os.environ["COLUMNS"] = "200"

import numpy as np
import pandas as pd
import pytest


def make_df(closes, volumes=None, start="2023-01-02"):
    """종가 리스트로 OHLCV 프레임을 만든다 (테스트 전용)."""
    closes = np.asarray(closes, dtype=float)
    index = pd.bdate_range(start, periods=len(closes))
    volumes = np.full(len(closes), 1_000_000.0) if volumes is None else np.asarray(volumes, dtype=float)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "close": closes,
            "volume": volumes,
        },
        index=index,
    )


@pytest.fixture
def uptrend():
    """꾸준히 오르는 차트 (이평 정배열이 성립)."""
    return make_df(100 * np.exp(np.linspace(0, 0.5, 300)))


@pytest.fixture
def downtrend():
    return make_df(100 * np.exp(np.linspace(0, -0.5, 300)))


@pytest.fixture
def flat():
    return make_df(np.full(300, 100.0) + np.sin(np.linspace(0, 20, 300)))
