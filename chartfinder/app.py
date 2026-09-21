"""Streamlit 웹 UI — 체크박스로 조건을 조합하고 결과를 차트로 확인한다.

실행: streamlit run chartfinder/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# `streamlit run chartfinder/app.py` 는 스크립트 폴더만 sys.path 에 넣으므로
# 패키지를 설치하지 않고도 실행되도록 저장소 루트를 추가한다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chartfinder import cache, presets as presets_mod
from chartfinder.charts import candle_chart
from chartfinder.conditions import by_category, get as get_condition
from chartfinder.datasource import MARKETS, universes
from chartfinder.presets import Preset
from chartfinder.screener import ConditionSpec, screen

PRESET_DIR = Path("presets")

st.set_page_config(page_title="차트 조건 검색기", page_icon="📈", layout="wide")


# --------------------------------------------------------------------------- 위젯 생성


def param_widget(cond_key: str, param, current: dict) -> object:
    """파라미터 스키마에서 위젯을 자동 생성한다."""
    wkey = f"{cond_key}__{param.name}"
    value = current.get(param.name, param.default)
    if param.type == "bool":
        return st.checkbox(param.label, value=bool(value), key=wkey)
    if param.type == "choice":
        options = list(param.choices)
        index = options.index(value) if value in options else 0
        return st.selectbox(param.label, options, index=index, key=wkey)
    if param.type == "float":
        return st.number_input(
            param.label,
            value=float(value),
            min_value=float(param.min) if param.min is not None else None,
            max_value=float(param.max) if param.max is not None else None,
            step=float(param.step or 0.5),
            key=wkey,
        )
    return st.number_input(
        param.label,
        value=int(value),
        min_value=int(param.min) if param.min is not None else None,
        max_value=int(param.max) if param.max is not None else None,
        step=1,
        key=wkey,
    )


def condition_builder(defaults: dict[str, dict]) -> list[ConditionSpec]:
    """카테고리별 체크박스 + 파라미터 위젯. 선택된 조건 목록을 돌려준다."""
    specs: list[ConditionSpec] = []
    for category, conds in by_category().items():
        with st.expander(f"**{category}** ({len(conds)})", expanded=category in defaults.get("__open__", {"추세"})):
            for cond in conds:
                preset_entry = defaults.get(cond.key)
                checked = st.checkbox(
                    f"{cond.label}  ·  `{cond.key}`",
                    value=preset_entry is not None,
                    key=f"chk_{cond.key}",
                    help=cond.description,
                )
                if not checked:
                    continue
                current = (preset_entry or {}).get("params", {})
                with st.container(border=True):
                    cols = st.columns(max(len(cond.params), 1) + 1)
                    values = {}
                    for col, param in zip(cols, cond.params):
                        with col:
                            values[param.name] = param_widget(cond.key, param, current)
                    with cols[-1]:
                        weight = st.slider(
                            "가중치", 0.5, 5.0,
                            float((preset_entry or {}).get("weight", 1.0)), 0.5,
                            key=f"w_{cond.key}",
                        )
                specs.append(ConditionSpec(key=cond.key, params=values, weight=weight))
    return specs


# --------------------------------------------------------------------------- 사이드바

st.title("📈 차트 조건 검색기")
st.caption("조건을 체크하면 **완전히 충족한 종목뿐 아니라 가장 근접한 종목**까지 점수순으로 보여줍니다.")

if "preset_defaults" not in st.session_state:
    st.session_state.preset_defaults = {}

with st.sidebar:
    st.header("대상")
    market = st.selectbox("시장", MARKETS, index=0,
                          format_func=lambda m: {"kr": "한국", "us": "미국", "demo": "데모(오프라인)"}[m])
    universe = st.selectbox("유니버스", universes(market))

    info = cache.stats(market)
    st.caption(f"캐시 {info['symbols']}종목 · 최근 {info['latest'] or '-'} · {info['size_mb']}MB")

    with st.popover("데이터 받기 / 갱신", use_container_width=True):
        years = st.number_input("과거 기간(년)", 1.0, 20.0, 2.0, 0.5)
        limit = st.number_input("종목 수 제한 (0=전체)", 0, 10000, 0, 50)
        force = st.checkbox("전체 재수집", value=False)
        if st.button("시작", type="primary", use_container_width=True):
            status = st.empty()
            bar = st.progress(0.0)
            tickers = cache.get_tickers(market, universe, refresh=force)
            symbols = [t.symbol for t in tickers]
            if limit:
                symbols = symbols[: int(limit)]

            def on_progress(done: int, total: int, symbol: str) -> None:
                bar.progress(min(done / max(total, 1), 1.0))
                status.text(f"{done}/{total} · {symbol}")

            stats = cache.update(market, universe, years=years, symbols=symbols,
                                 force=force, progress=on_progress)
            bar.progress(1.0)
            st.success(f"갱신 {stats['updated']} · 최신 {stats['skipped']} · 실패 {stats['failed']}")

    st.header("결과 옵션")
    top = st.slider("상위 N종목", 5, 200, 30, 5)
    min_score = st.slider("최소 점수", 0.0, 1.0, 0.0, 0.05)
    strict = st.toggle("엄격 모드 (모든 조건 완전 충족만)", value=False,
                       help="끄면 근접도 점수로 정렬해 '아깝게 놓친' 종목도 보여줍니다.")

    st.header("프리셋")
    preset_paths = presets_mod.list_presets(PRESET_DIR)
    if preset_paths:
        chosen = st.selectbox("불러오기", ["-"] + [p.name for p in preset_paths])
        if chosen != "-" and st.button("적용", use_container_width=True):
            loaded = presets_mod.load(PRESET_DIR / chosen)
            st.session_state.preset_defaults = {
                s.key: {"params": get_condition(s.key).resolve(s.params), "weight": s.weight}
                for s in loaded.conditions
            }
            for key in [k for k in st.session_state if k.startswith(("chk_", "w_"))]:
                del st.session_state[key]
            st.rerun()

# --------------------------------------------------------------------------- 조건 빌더

st.subheader("조건 선택")
specs = condition_builder(st.session_state.preset_defaults)

left, right = st.columns([1, 3])
with left:
    run = st.button("🔍 검색", type="primary", use_container_width=True, disabled=not specs)
with right:
    if specs:
        st.caption(" · ".join(f"{get_condition(s.key).label}(w={s.weight:g})" for s in specs))
    else:
        st.caption("조건을 하나 이상 선택하세요.")

with st.sidebar:
    if specs:
        with st.popover("현재 조건 저장", use_container_width=True):
            name = st.text_input("프리셋 이름", value="내 조건")
            if st.button("저장", use_container_width=True):
                path = presets_mod.save(
                    Preset(name=name, market=market, universe=universe, conditions=specs),
                    PRESET_DIR / f"{name.replace(' ', '_')}.yaml",
                )
                st.success(f"저장됨: {path}")

# --------------------------------------------------------------------------- 실행

if run:
    tickers = cache.get_tickers(market, universe)
    bar = st.progress(0.0, text="채점 중")
    result = screen(
        market, specs, tickers=tickers, strict=strict, min_score=min_score, top=top,
        progress=lambda done, total: bar.progress(min(done / max(total, 1), 1.0),
                                                  text=f"채점 중 {done}/{total}"),
    )
    bar.empty()
    st.session_state.result = result
    st.session_state.result_market = market
    st.session_state.result_specs = [s.key for s in specs]

result = st.session_state.get("result")
if result is not None:
    if result.empty:
        st.warning("조건에 맞는 종목이 없습니다. 캐시가 비어 있다면 사이드바에서 데이터를 먼저 받으세요.")
    else:
        st.subheader(f"결과 {len(result)}종목")
        score_cols = [c for c in result.columns if c.startswith("s_")]
        display = result.rename(
            columns={"symbol": "종목", "name": "이름", "score": "점수", "matched": "충족",
                     "close": "종가", "chg_pct": "등락%", "date": "기준일",
                     **{c: get_condition(c[2:]).label for c in score_cols}}
        )
        event = st.dataframe(
            display, use_container_width=True, hide_index=True,
            on_select="rerun", selection_mode="single-row",
            column_config={
                "점수": st.column_config.ProgressColumn("점수", min_value=0.0, max_value=1.0, format="%.3f"),
                **{
                    get_condition(c[2:]).label: st.column_config.NumberColumn(format="%.2f")
                    for c in score_cols
                },
            },
        )

        rows = event.selection.rows if hasattr(event, "selection") else []
        idx = rows[0] if rows else 0
        row = result.iloc[idx]
        df = cache.load(st.session_state.result_market, row["symbol"])
        if df is not None and not df.empty:
            detail = " · ".join(
                f"{get_condition(c[2:]).label} {row[c]:.2f}" for c in score_cols
            )
            st.plotly_chart(
                candle_chart(df, f"{row['symbol']} {row['name']} — 점수 {row['score']:.3f}"),
                use_container_width=True,
            )
            st.caption(detail)

        st.download_button(
            "CSV 내려받기",
            result.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"scan_{st.session_state.result_market}.csv",
            mime="text/csv",
        )
