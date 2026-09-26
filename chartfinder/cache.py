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
from .datasource.base import FLOW_COLUMNS, PROFILE_FIELDS

#: 증분 갱신 시 겹쳐서 다시 받는 일수 (수정주가 반영분 보정)
OVERLAP_DAYS = 7
#: 요청 기간이 캐시보다 이만큼 더 앞서면 과거를 다시 받는다.
#: 상장일이 늦은 종목까지 매번 다시 받지 않도록 여유를 둔다.
BACKFILL_SLACK_DAYS = 120
#: 종목 목록 캐시 유효기간
TICKER_TTL_DAYS = 7
#: 재무 캐시 유효기간. 분기마다 바뀌므로 자주 받을 이유가 없다.
FUNDAMENTAL_TTL_DAYS = 30

#: 종목 정보(시가총액·주식수·지분율)는 하루 한 번이면 충분하다
PROFILE_TTL_DAYS = 1
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


def _index_path(market: str, name: str) -> Path:
    path = cache_home() / "indices" / f"{market}_{name}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _profile_path(market: str) -> Path:
    path = cache_home() / "profiles" / f"{market}.parquet"
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
    workers: int | None = None,
    progress: ProgressFn | None = None,
) -> dict[str, object]:
    """재무 데이터를 받아 캐시한다.

    종목당 1회 요청이다. 순차로 받으면 2,600종목에 한 시간이 넘으므로
    시세와 같은 수의 요청을 동시에 보낸다.
    """
    source = get_source(market)
    if not getattr(source, "supports_fundamentals", False):
        return {"updated": 0, "skipped": 0, "failed": 0,
                "error": f"{market} 시장은 재무 수집을 지원하지 않습니다."}

    if symbols is None:
        symbols = [t.symbol for t in get_tickers(market, universe)]

    if workers:
        source.max_workers = max(1, workers)

    stats: dict[str, object] = {"updated": 0, "skipped": 0, "failed": 0}
    todo = [s for s in symbols if force or not fundamentals_fresh(market, s)]
    stats["skipped"] = len(symbols) - len(todo)

    total = len(symbols)
    done = stats["skipped"]
    step = _chunk_size(source)
    for i in range(0, len(todo), step):
        chunk = todo[i : i + step]
        outcomes = _parallel(source.fetch_fundamentals, chunk, source.max_workers)
        for symbol in chunk:
            outcome = outcomes.get(symbol)
            if isinstance(outcome, Exception):
                stats["failed"] += 1
                stats.setdefault("error", f"{type(outcome).__name__}: {outcome}")
            elif outcome is None or outcome.empty:
                stats["failed"] += 1
                stats.setdefault("error", f"재무 응답이 비어 있습니다 ({symbol}).")
            else:
                save_fundamentals(market, symbol, outcome)
                stats["updated"] += 1
                stats["source"] = getattr(source, "_fundamental_provider", None)
            done += 1
            if progress:
                progress(done, total, symbol)
    if progress:
        progress(total, total, "")
    return stats


# --------------------------------------------------------------------------- 지수


def load_index(market: str, name: str) -> pd.DataFrame | None:
    """지수 일봉. 상대강도 계산의 비교 기준이다."""
    path = _index_path(market, name)
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
    except Exception:
        return None
    return None if df.empty else df


def has_indices(market: str) -> bool:
    source = get_source(market)
    names = set((getattr(source, "benchmarks", None) or {}).values())
    return bool(names) and all(load_index(market, n) is not None for n in names)


def update_indices(
    market: str, years: float = 2.0, force: bool = False
) -> dict[str, object]:
    """비교 기준 지수를 받아 캐시한다.

    지수는 거래소마다 하나뿐이라 요청이 1~2회다. 종목 수와 무관하게 싸다.
    """
    source = get_source(market)
    names = sorted(set((getattr(source, "benchmarks", None) or {}).values()))
    if not names:
        return {"updated": 0, "error": f"{market} 시장은 비교 지수가 없습니다."}

    today = date.today()
    fetch_end = today + timedelta(days=1)
    full_start = today - timedelta(days=int(365.25 * years) + 40)

    stats: dict[str, object] = {"updated": 0, "failed": 0}
    for name in names:
        existing = None if force else load_index(market, name)
        # 지수도 종목과 같은 이유로 소급이 필요하다 (-y 를 늘렸을 때)
        start = full_start
        if existing is not None and existing.index[0].date() <= full_start + timedelta(
            days=BACKFILL_SLACK_DAYS
        ):
            start = existing.index[-1].date() - timedelta(days=OVERLAP_DAYS)
        try:
            fetched = source.fetch_index(name, start, fetch_end)
        except Exception as exc:
            stats["failed"] += 1
            stats.setdefault("error", f"{name}: {type(exc).__name__}: {exc}")
            continue
        if fetched is None or fetched.empty:
            stats["failed"] += 1
            stats.setdefault("error", f"{name}: 지수 응답이 비어 있습니다.")
            continue
        merge(existing, fetched).to_parquet(_index_path(market, name))
        stats["updated"] += 1
    return stats


# --------------------------------------------------------------------------- 종목 정보


def load_profiles(market: str) -> pd.DataFrame | None:
    """시장 전체 종목 정보 스냅샷 (index=종목코드). 없으면 None."""
    path = _profile_path(market)
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
    except Exception:
        return None
    return None if df.empty else df


def save_profiles(market: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    df.index = df.index.astype(str)
    df.to_parquet(_profile_path(market))


def profiles_fresh(market: str, ttl_days: int = PROFILE_TTL_DAYS) -> bool:
    path = _profile_path(market)
    if not path.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age.days < ttl_days


def has_profiles(market: str) -> bool:
    df = load_profiles(market)
    return df is not None and not df.empty


def update_profiles(
    market: str,
    universe: str = "all",
    symbols: list[str] | None = None,
    force: bool = False,
    progress: ProgressFn | None = None,
) -> dict[str, object]:
    """종목 정보 스냅샷을 받아 캐시한다.

    항목마다 출처가 달라 일부만 채워질 수 있다. 어떤 항목이 몇 종목
    채워졌는지 stats["filled"] 에 남겨, 조건이 조용히 죽는 일을 막는다.
    """
    source = get_source(market)
    if not getattr(source, "supports_profiles", False):
        return {"updated": 0, "error": f"{market} 시장은 종목 정보 수집을 지원하지 않습니다."}

    if not force and profiles_fresh(market):
        existing = load_profiles(market)
        return {"updated": 0, "skipped": len(existing) if existing is not None else 0}

    if symbols is None:
        symbols = [t.symbol for t in get_tickers(market, universe)]

    stats: dict[str, object] = {"updated": 0}
    try:
        df = source.fetch_profiles(symbols, universe)
    except Exception as exc:
        return {"updated": 0, "error": f"{type(exc).__name__}: {exc}"}
    if df is None or df.empty:
        return {"updated": 0, "error": "종목 정보 응답이 비어 있습니다."}

    df = df.reindex(columns=[c for c in PROFILE_FIELDS if c in df.columns])
    save_profiles(market, df)
    stats["updated"] = len(df)
    stats["filled"] = {col: int(df[col].notna().sum()) for col in df.columns}
    stats["missing"] = [f for f in PROFILE_FIELDS if f not in df.columns]
    if progress:
        progress(len(df), len(df), "")
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


def first_date(market: str, symbol: str) -> date | None:
    df = load(market, symbol)
    if df is None or df.empty:
        return None
    return df.index[0].date()


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
    """투자자별 순매수를 받아 일봉 프레임에 컬럼으로 붙인다.

    (프레임, 실제로 반영된 행 수)를 돌려준다 — 빈 응답을 성공으로 세지 않기 위해서.
    """
    return apply_flows(df, source.fetch_flows(symbol, start, end))


def apply_flows(df: pd.DataFrame, flows: pd.DataFrame | None) -> tuple[pd.DataFrame, int]:
    """이미 받아둔 수급을 일봉 프레임에 붙인다 (네트워크 없음).

    증분 갱신 시 받아온 구간만 덮어쓰고 그 밖의 기존 값은 보존한다.
    """
    if flows is None or flows.empty:
        return df, 0
    for col in flows.columns:
        if col not in df.columns:
            df[col] = pd.NA
    df.update(flows)
    return df, int(len(df.index.intersection(flows.index)))


def _chunk_size(source) -> int:
    """한 번에 처리할 종목 수. 동시 요청 수보다 작으면 병렬이 무의미하다."""
    batch = getattr(source, "batch_size", 1) or 1
    return max(batch, getattr(source, "max_workers", 1) or 1, 1)


def _parallel(fn: Callable[[str], object], symbols: list[str], workers: int) -> dict[str, object]:
    """종목별 네트워크 호출을 동시에 보낸다. 예외는 그 종목의 값으로 담아 돌려준다.

    수급·재무는 종목당 1회 요청이라 순차로 받으면 시세를 아무리 빨리 받아도
    전체 시간이 줄지 않는다. 여기가 실제 병목이다.
    """
    if workers <= 1 or len(symbols) < 2:
        results: dict[str, object] = {}
        for symbol in symbols:
            try:
                results[symbol] = fn(symbol)
            except Exception as exc:
                results[symbol] = exc
        return results

    from concurrent.futures import ThreadPoolExecutor

    results = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, symbol): symbol for symbol in symbols}
        for future, symbol in futures.items():
            try:
                results[symbol] = future.result()
            except Exception as exc:
                results[symbol] = exc
    return results


def update(
    market: str,
    universe: str = "all",
    years: float = 2.0,
    symbols: list[str] | None = None,
    force: bool = False,
    flows: bool = False,
    workers: int | None = None,
    progress: ProgressFn | None = None,
) -> dict[str, int]:
    """캐시를 최신 상태로 만든다.

    flows=True면 외국인·기관 순매수도 함께 받는다 (지원하는 시장만, 종목당 1회 요청).
    {'updated': n, 'skipped': n, 'failed': n, 'flows': n} 반환.
    수급 수집이 실패하면 첫 실패 사유가 'flow_error' 에 담긴다.
    """
    source = get_source(market)
    if workers:
        # 종목당 1회 요청인 소스는 동시 요청 수가 곧 속도다
        source.max_workers = max(1, workers)
    if symbols is None:
        symbols = [t.symbol for t in get_tickers(market, universe, refresh=force)]

    today = date.today()
    # 조회 종료일은 시세·수급 모두 동일하게 쓴다 (하루라도 어긋나면 마지막 행이 빈다)
    fetch_end = today + timedelta(days=1)
    full_start = today - timedelta(days=int(365.25 * years) + 40)

    stats: dict[str, object] = {
        "updated": 0, "skipped": 0, "failed": 0, "flows": 0, "backfilled": 0
    }

    want_flows = flows and getattr(source, "supports_flows", False)
    last_session = _last_expected_session(today)

    # 비교 지수는 거래소마다 하나뿐이라 요청이 1~2회다. 상대강도 조건이
    # 조용히 0점이 되는 것을 막으려고 시세를 받을 때 같이 받아 둔다.
    if getattr(source, "benchmarks", None):
        index_stats = update_indices(market, years=years, force=force)
        stats["indices"] = index_stats.get("updated", 0)
        if index_stats.get("error"):
            stats["index_error"] = index_stats["error"]

    # 어디까지 받아야 하는지에 따라 종목을 묶는다 (배치 다운로드용)
    buckets: dict[date, list[str]] = {}
    # 시세는 최신이지만 수급만 빠진 종목 (시세를 다시 받을 필요가 없다)
    flows_only: list[str] = []
    for sym in symbols:
        if force:
            start = full_start
        else:
            last = last_date(market, sym)
            first = first_date(market, sym)
            # 요청한 기간이 캐시보다 앞서면 과거를 소급해서 채운다.
            # 증분만 받으면 `-y 5` 로 늘려도 캐시는 예전 길이 그대로 남는다.
            needs_backfill = first is not None and first > full_start + timedelta(days=BACKFILL_SLACK_DAYS)

            if last is None:
                start = full_start
            elif needs_backfill:
                start = full_start
                stats["backfilled"] += 1
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

    # 묶음마다 따로 받으면 시작일이 제각각인 종목은 1개짜리 묶음이 되어
    # 동시 요청이 아예 걸리지 않는다. 시작일 순으로 늘어놓고 묶음 경계를
    # 무시한 채 잘라서, 모든 종목이 같은 크기의 조각에 들어가게 한다.
    # 조각 안에서는 가장 이른 시작일을 쓴다 (더 받아도 병합 때 흡수된다).
    # batch_size 는 진행률 갱신 단위일 뿐이다. 그게 1인 소스에서는 조각도
    # 1종목이 되어 동시 요청이 걸리지 않으므로, 하한을 동시 요청 수로 올린다.
    step = _chunk_size(source)
    ordered = sorted(
        ((start, sym) for start, syms in buckets.items() for sym in syms),
        key=lambda pair: pair[0],
    )
    for i in range(0, len(ordered), step):
        window = ordered[i : i + step]
        start = min(pair[0] for pair in window)
        chunk = [pair[1] for pair in window]
        try:
            fetched = source.fetch_many(chunk, start, fetch_end)
        except Exception:
            fetched = {}

        # 수급도 조각 단위로 한꺼번에 받는다. 종목마다 순서대로 받으면
        # 시세를 병렬로 받은 효과가 여기서 전부 상쇄된다.
        flow_frames: dict[str, object] = {}
        if want_flows:
            flow_frames = _parallel(
                lambda sym: source.fetch_flows(sym, start, fetch_end),
                chunk,
                source.max_workers,
            )

        for sym in chunk:
            df = fetched.get(sym)
            if df is None or df.empty:
                stats["failed"] += 1
            else:
                combined = merge(None if force else load(market, sym), df)
                if want_flows:
                    # 수급 실패가 시세 저장을 막지는 않되, 이유는 남긴다
                    outcome = flow_frames.get(sym)
                    if isinstance(outcome, Exception):
                        stats.setdefault(
                            "flow_error", f"{type(outcome).__name__}: {outcome}"
                        )
                    else:
                        combined, applied = apply_flows(combined, outcome)
                        if applied:
                            stats["flows"] += 1
                            stats["flow_source"] = getattr(source, "_flow_provider", None)
                        else:
                            stats.setdefault(
                                "flow_error",
                                f"수급 응답이 비어 있습니다 ({sym}, {start}~{today}).",
                            )
                save(market, sym, combined)
                stats["updated"] += 1
            done += 1
            if progress:
                progress(done, total, sym)

    # 시세는 그대로 두고 수급만 채운다 (여기도 조각 단위로 동시에 받는다)
    for i in range(0, len(flows_only), step):
        chunk = flows_only[i : i + step]
        frames = {sym: load(market, sym) for sym in chunk}
        starts = {
            sym: _flow_start(df, full_start)
            for sym, df in frames.items()
            if df is not None and not df.empty
        }
        outcomes = _parallel(
            lambda sym: source.fetch_flows(sym, starts[sym], fetch_end),
            list(starts),
            source.max_workers,
        )
        for sym in chunk:
            df = frames.get(sym)
            if df is not None and not df.empty:
                outcome = outcomes.get(sym)
                if isinstance(outcome, Exception):
                    stats.setdefault("flow_error", f"{type(outcome).__name__}: {outcome}")
                else:
                    updated, applied = apply_flows(df, outcome)
                    if applied:
                        save(market, sym, updated)
                        stats["flows"] += 1
                        stats["flow_source"] = getattr(source, "_flow_provider", None)
                    else:
                        stats.setdefault(
                            "flow_error",
                            f"수급 응답이 비어 있습니다 "
                            f"({sym}, {starts[sym]}~{today}).",
                        )
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


def has_flows(market: str, sample: int = 5) -> bool:
    """수급 컬럼이 들어 있는 캐시가 있는지 (표본만 확인)."""
    folder = cache_home() / "prices" / market
    if not folder.exists():
        return False
    for path in sorted(folder.glob("*.parquet"))[:sample]:
        try:
            columns = pd.read_parquet(path).columns
        except Exception:
            continue
        if any(col in columns for col in FLOW_COLUMNS):
            return True
    return False


def has_fundamentals(market: str) -> bool:
    folder = cache_home() / "fundamentals" / market
    return folder.exists() and any(folder.glob("*.parquet"))


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
