"""시장별 데이터 소스."""

from __future__ import annotations

from .base import DataSource, Ticker, normalize_ohlcv

_SOURCES = {
    "kr": "chartfinder.datasource.krx:KrxSource",
    "us": "chartfinder.datasource.us:UsSource",
    "demo": "chartfinder.datasource.demo:DemoSource",  # 오프라인 시험용 합성 데이터
}

MARKETS = tuple(_SOURCES)

_cache: dict[str, DataSource] = {}


def get_source(market: str) -> DataSource:
    """시장 코드로 데이터 소스를 가져온다 (프로세스 내 재사용)."""
    market = market.lower()
    if market not in _SOURCES:
        raise ValueError(f"지원하지 않는 시장: {market} (가능: {MARKETS})")
    if market not in _cache:
        import importlib

        module_path, cls_name = _SOURCES[market].split(":")
        _cache[market] = getattr(importlib.import_module(module_path), cls_name)()
    return _cache[market]


__all__ = ["DataSource", "Ticker", "normalize_ohlcv", "get_source", "MARKETS"]
