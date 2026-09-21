"""로컬 일봉 캐시.

한 번 받아두면 이후 스캔은 네트워크 없이 수 초 안에 끝난다.
  <home>/tickers/<market>_<universe>.json   종목 목록
  <home>/prices/<market>/<symbol>.parquet   일봉
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from .datasource import Ticker, get_source

#: 증분 갱신 시 겹쳐서 다시 받는 일수 (수정주가 반영분 보정)
OVERLAP_DAYS = 7
#: 종목 목록 캐시 유효기간
TICKER_TTL_DAYS = 7

ProgressFn = Callable[[int, int, str], None]


def cache_home() -> Path:
    path = Path(os.environ.get("CHARTFINDER_HOME", Path.home() / ".chartfinder"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _price_path(market: str, symbol: str) -> Path:
    safe = symbol.replace("/", "_").replace("\\", "_")
    path = cache_home() / "prices" / market / f"{safe}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _ticker_path(market: str, universe: str) -> Path:
    path = cache_home() / "tickers" / f"{market}_{universe}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


# --------------------------------------------------------------------------- 종목 목록


def get_tickers(market: str, universe: str, refresh: bool = False) -> list[Ticker]:
    path = _ticker_path(market, universe)
    if not refresh and path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched = date.fromisoformat(payload["fetched_at"])
        if (date.today() - fetched).days <= TICKER_TTL_DAYS:
            return [Ticker(**t) for t in payload["tickers"]]

    tickers = get_source(market).list_tickers(universe)
    path.write_text(
        json.dumps(
            {"fetched_at": date.today().isoformat(), "tickers": [asdict(t) for t in tickers]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tickers


# --------------------------------------------------------------------------- 시세


def load(market: str, symbol: str) -> pd.DataFrame | None:
    path = _price_path(market, symbol)
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception:
        return None


def load_many(market: str, symbols: Iterable[str]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = load(market, sym)
        if df is not None and not df.empty:
            out[sym] = df
    return out


def save(market: str, symbol: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    df.to_parquet(_price_path(market, symbol))


def last_date(market: str, symbol: str) -> date | None:
    df = load(market, symbol)
    if df is None or df.empty:
        return None
    return df.index[-1].date()


def merge(old: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
    if old is None or old.empty:
        return new
    if new is None or new.empty:
        return old
    combined = pd.concat([old, new])
    combined = combined[~combined.index.duplicated(keep="last")].sort_index()
    return combined


def update(
    market: str,
    universe: str = "all",
    years: float = 2.0,
    symbols: list[str] | None = None,
    force: bool = False,
    progress: ProgressFn | None = None,
) -> dict[str, int]:
    """캐시를 최신 상태로 만든다. {'updated': n, 'skipped': n, 'failed': n} 반환."""
    source = get_source(market)
    if symbols is None:
        symbols = [t.symbol for t in get_tickers(market, universe, refresh=force)]

    today = date.today()
    full_start = today - timedelta(days=int(365.25 * years) + 40)

    stats = {"updated": 0, "skipped": 0, "failed": 0}

    # 어디까지 받아야 하는지에 따라 종목을 묶는다 (배치 다운로드용)
    buckets: dict[date, list[str]] = {}
    for sym in symbols:
        if force:
            start = full_start
        else:
            last = last_date(market, sym)
            if last is None:
                start = full_start
            elif last >= _last_expected_session(today):
                stats["skipped"] += 1
                continue
            else:
                start = last - timedelta(days=OVERLAP_DAYS)
        buckets.setdefault(start, []).append(sym)

    total = sum(len(v) for v in buckets.values())
    done = 0
    for start, syms in buckets.items():
        step = getattr(source, "batch_size", 1) or 1
        for i in range(0, len(syms), step):
            chunk = syms[i : i + step]
            try:
                fetched = source.fetch_many(chunk, start, today + timedelta(days=1))
            except Exception:
                fetched = {}
            for sym in chunk:
                df = fetched.get(sym)
                if df is None or df.empty:
                    stats["failed"] += 1
                else:
                    save(market, sym, merge(None if force else load(market, sym), df))
                    stats["updated"] += 1
                done += 1
                if progress:
                    progress(done, total, sym)
    return stats


def _last_expected_session(today: date) -> date:
    """가장 최근 영업일(주말만 고려). 장 마감 전이면 전 영업일."""
    ref = today if datetime.now().hour >= 18 else today - timedelta(days=1)
    while ref.weekday() >= 5:  # 토=5, 일=6
        ref -= timedelta(days=1)
    return ref


def stats(market: str) -> dict[str, object]:
    folder = cache_home() / "prices" / market
    files = sorted(folder.glob("*.parquet")) if folder.exists() else []
    latest: date | None = None
    for path in files[:200]:  # 표본만 확인
        try:
            idx = pd.read_parquet(path).index
        except Exception:
            continue
        if len(idx):
            d = idx[-1].date()
            latest = d if latest is None or d > latest else latest
    size_mb = sum(f.stat().st_size for f in files) / 1e6
    return {"symbols": len(files), "latest": latest, "size_mb": round(size_mb, 1)}
