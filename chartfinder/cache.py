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
from .datasource.base import FLOW_COLUMNS

#: 증분 갱신 시 겹쳐서 다시 받는 일수 (수정주가 반영분 보정)
OVERLAP_DAYS = 7
#: 종목 목록 캐시 유효기간
TICKER_TTL_DAYS = 7
#: 재무 캐시 유효기간. 분기마다 바뀌므로 자주 받을 이유가 없다.
FUNDAMENTAL_TTL_DAYS = 30
#: 수급을 처음 받을 때 거슬러 올라갈 일수.
#: 수급 조건이 보는 구간은 길어야 수십 일이라 시세만큼 길게 받을 이유가 없다.
FLOW_HISTORY_DAYS = 120

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


def _fundamental_path(market: str, symbol: str) -> Path:
    safe = symbol.replace("/", "_").replace("\\", "_")
    path = cache_home() / "fundamentals" / market / f"{safe}.parquet"
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


# --------------------------------------------------------------------------- 재무


def load_fundamentals(market: str, symbol: str) -> pd.DataFrame | None:
    path = _fundamental_path(market, symbol)
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception:
        return None


def save_fundamentals(market: str, symbol: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    df.to_parquet(_fundamental_path(market, symbol))


def fundamentals_fresh(market: str, symbol: str, ttl_days: int = FUNDAMENTAL_TTL_DAYS) -> bool:
    path = _fundamental_path(market, symbol)
    if not path.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age.days < ttl_days


def update_fundamentals(
    market: str,
    universe: str = "all",
    symbols: list[str] | None = None,
    force: bool = False,
    progress: ProgressFn | None = None,
) -> dict[str, object]:
    """재무 데이터를 받아 캐시한다. 종목당 1회 요청이라 시세보다 느리다."""
    source = get_source(market)
    if not getattr(source, "supports_fundamentals", False):
        return {"updated": 0, "skipped": 0, "failed": 0,
                "error": f"{market} 시장은 재무 수집을 지원하지 않습니다."}

    if symbols is None:
        symbols = [t.symbol for t in get_tickers(market, universe)]

    stats: dict[str, object] = {"updated": 0, "skipped": 0, "failed": 0}
    total = len(symbols)
    for done, symbol in enumerate(symbols, start=1):
        if progress:
            progress(done, total, symbol)
        if not force and fundamentals_fresh(market, symbol):
            stats["skipped"] += 1
            continue
        try:
            df = source.fetch_fundamentals(symbol)
        except Exception as exc:
            stats["failed"] += 1
            stats.setdefault("error", f"{type(exc).__name__}: {exc}")
            continue
        if df is None or df.empty:
            stats["failed"] += 1
            stats.setdefault("error", f"재무 응답이 비어 있습니다 ({symbol}).")
            continue
        save_fundamentals(market, symbol, df)
        stats["updated"] += 1
        stats["source"] = getattr(source, "_fundamental_provider", None)
    return stats


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


def attach_flows(
    source, symbol: str, df: pd.DataFrame, start: date, end: date
) -> tuple[pd.DataFrame, int]:
    """투자자별 순매수를 일봉 프레임에 컬럼으로 붙인다.

    증분 갱신 시 받아온 구간만 덮어쓰고 그 밖의 기존 값은 보존한다.
    (프레임, 실제로 반영된 행 수)를 돌려준다 — 빈 응답을 성공으로 세지 않기 위해서.
    """
    flows = source.fetch_flows(symbol, start, end)
    if flows is None or flows.empty:
        return df, 0
    for col in flows.columns:
        if col not in df.columns:
            df[col] = pd.NA
    df.update(flows)
    return df, int(len(df.index.intersection(flows.index)))


def update(
    market: str,
    universe: str = "all",
    years: float = 2.0,
    symbols: list[str] | None = None,
    force: bool = False,
    flows: bool = False,
    progress: ProgressFn | None = None,
) -> dict[str, int]:
    """캐시를 최신 상태로 만든다.

    flows=True면 외국인·기관 순매수도 함께 받는다 (지원하는 시장만, 종목당 1회 요청).
    {'updated': n, 'skipped': n, 'failed': n, 'flows': n} 반환.
    수급 수집이 실패하면 첫 실패 사유가 'flow_error' 에 담긴다.
    """
    source = get_source(market)
    if symbols is None:
        symbols = [t.symbol for t in get_tickers(market, universe, refresh=force)]

    today = date.today()
    # 조회 종료일은 시세·수급 모두 동일하게 쓴다 (하루라도 어긋나면 마지막 행이 빈다)
    fetch_end = today + timedelta(days=1)
    full_start = today - timedelta(days=int(365.25 * years) + 40)

    stats: dict[str, object] = {"updated": 0, "skipped": 0, "failed": 0, "flows": 0}

    want_flows = flows and getattr(source, "supports_flows", False)
    last_session = _last_expected_session(today)

    # 어디까지 받아야 하는지에 따라 종목을 묶는다 (배치 다운로드용)
    buckets: dict[date, list[str]] = {}
    # 시세는 최신이지만 수급만 빠진 종목 (시세를 다시 받을 필요가 없다)
    flows_only: list[str] = []
    for sym in symbols:
        if force:
            start = full_start
        else:
            last = last_date(market, sym)
            if last is None:
                start = full_start
            elif last >= last_session:
                if want_flows and not flows_up_to_date(load(market, sym), last_session):
                    flows_only.append(sym)
                else:
                    stats["skipped"] += 1
                continue
            else:
                start = last - timedelta(days=OVERLAP_DAYS)
        buckets.setdefault(start, []).append(sym)

    total = sum(len(v) for v in buckets.values()) + len(flows_only)
    done = 0
    for start, syms in buckets.items():
        step = getattr(source, "batch_size", 1) or 1
        for i in range(0, len(syms), step):
            chunk = syms[i : i + step]
            try:
                fetched = source.fetch_many(chunk, start, fetch_end)
            except Exception:
                fetched = {}
            for sym in chunk:
                df = fetched.get(sym)
                if df is None or df.empty:
                    stats["failed"] += 1
                else:
                    combined = merge(None if force else load(market, sym), df)
                    if want_flows:
                        # 수급 실패가 시세 저장을 막지는 않되, 이유는 남긴다
                        try:
                            combined, applied = attach_flows(
                                source, sym, combined, start, fetch_end
                            )
                            if applied:
                                stats["flows"] += 1
                                stats["flow_source"] = getattr(source, "_flow_provider", None)
                            else:
                                stats.setdefault(
                                    "flow_error",
                                    f"수급 응답이 비어 있습니다 ({sym}, {start}~{today}).",
                                )
                        except Exception as exc:
                            stats.setdefault("flow_error", f"{type(exc).__name__}: {exc}")
                    save(market, sym, combined)
                    stats["updated"] += 1
                done += 1
                if progress:
                    progress(done, total, sym)

    # 시세는 그대로 두고 수급만 채운다
    for sym in flows_only:
        df = load(market, sym)
        if df is not None and not df.empty:
            try:
                updated, applied = attach_flows(
                    source, sym, df, _flow_start(df, full_start), fetch_end
                )
                if applied:
                    save(market, sym, updated)
                    stats["flows"] += 1
                    stats["flow_source"] = getattr(source, "_flow_provider", None)
                else:
                    stats.setdefault(
                        "flow_error",
                        f"수급 응답이 비어 있습니다 "
                        f"({sym}, {_flow_start(df, full_start)}~{today}).",
                    )
            except Exception as exc:
                stats.setdefault("flow_error", f"{type(exc).__name__}: {exc}")
        done += 1
        if progress:
            progress(done, total, sym)
    return stats


def flows_up_to_date(df: pd.DataFrame | None, ref: date) -> bool:
    """수급 컬럼이 있고 ref 일자까지 값이 채워져 있는지."""
    if df is None or df.empty:
        return False
    present = [c for c in FLOW_COLUMNS if c in df.columns]
    if not present:
        return False
    filled = df[present].dropna(how="all")
    return bool(len(filled)) and filled.index[-1].date() >= ref


def _flow_start(df: pd.DataFrame, fallback: date) -> date:
    """수급을 어디부터 받을지. 이미 받은 구간이 있으면 그 끝에서 조금 겹쳐서."""
    present = [c for c in FLOW_COLUMNS if c in df.columns]
    if present:
        filled = df[present].dropna(how="all")
        if len(filled):
            return filled.index[-1].date() - timedelta(days=OVERLAP_DAYS)
    earliest = date.today() - timedelta(days=FLOW_HISTORY_DAYS)
    return max(fallback, df.index[0].date(), earliest)


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
