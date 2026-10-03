"""캔들차트 생성 (웹 UI와 데스크톱 UI가 공유).

조건에 쓰는 지표를 차트에도 그린다 — 점수만 보면 왜 뽑혔는지 알 수 없다.
이름·축·말풍선은 모두 한국어다.

구성 (위에서 아래로):
  1. 가격 — 캔들 + 이동평균선 + 볼린저 + 엔벨로프, 오른쪽에 매물대
  2. 거래량 — 막대 + 20일 평균선
  3. 수급 — 외국인·기관 순매수 (받아둔 경우만)
  4. RSI — 30/70 기준선
  5. MACD — 히스토그램 + 시그널선
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import indicators as ind

#: 이동평균선 기간과 색
MA_LINES = ((5, "#e377c2"), (20, "#ff7f0e"), (60, "#2ca02c"), (120, "#9467bd"))

#: 한국 증시 관행대로 상승은 빨강, 하락은 파랑
UP, DOWN = "#d62728", "#1f77b4"

#: 매물대를 몇 칸으로 나눠 그릴지
PROFILE_BINS = 40


def _korean_hover(name: str, unit: str = "") -> str:
    return f"{name} %{{y:,.0f}}{unit}<extra></extra>"


def candle_chart(df: pd.DataFrame, title: str, months: int = 12):
    """캔들 + 지표 + 매물대 + 수급 차트. plotly Figure 를 돌려준다."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    view = df.tail(months * 21)
    has_flows = {"foreign_net", "inst_net"} <= set(df.columns) and (
        pd.to_numeric(df["foreign_net"], errors="coerce").notna().any()
    )

    rows = ["가격", "거래량"] + (["수급"] if has_flows else []) + ["RSI", "MACD"]
    heights = [0.44, 0.14] + ([0.14] if has_flows else []) + [0.14, 0.14]
    total = sum(heights)
    heights = [h / total for h in heights]

    fig = make_subplots(
        rows=len(rows), cols=2,
        shared_xaxes=True, shared_yaxes=False,
        vertical_spacing=0.025, horizontal_spacing=0.01,
        row_heights=heights, column_widths=[0.86, 0.14],
        specs=[[{}, {"rowspan": len(rows)} if i == 0 else None] for i in range(len(rows))],
        subplot_titles=(None,) * (len(rows) * 2),
    )
    at = {name: i + 1 for i, name in enumerate(rows)}

    # ---------------------------------------------------------------- 1. 가격
    fig.add_trace(
        go.Candlestick(
            x=view.index, open=view["open"], high=view["high"],
            low=view["low"], close=view["close"], name="주가",
            increasing_line_color=UP, decreasing_line_color=DOWN,
            increasing_fillcolor=UP, decreasing_fillcolor=DOWN,
            hovertext=[
                f"시가 {o:,.0f}<br>고가 {h:,.0f}<br>저가 {l:,.0f}<br>종가 {c:,.0f}"
                for o, h, l, c in zip(view["open"], view["high"], view["low"], view["close"])
            ],
            hoverinfo="x+text",
        ),
        row=at["가격"], col=1,
    )
    for period, color in MA_LINES:
        if len(df) < period:
            continue
        fig.add_trace(
            go.Scatter(
                x=view.index, y=ind.sma(df["close"], period).reindex(view.index),
                name=f"{period}일선", line=dict(width=1.1, color=color),
                hovertemplate=_korean_hover(f"{period}일선", "원"),
            ),
            row=at["가격"], col=1,
        )
    if len(df) >= 20:
        lower, _, upper = ind.bollinger(df["close"], 20, 2.0)
        for label, series, dash in (("볼린저 상단", upper, "dot"), ("볼린저 하단", lower, "dot")):
            fig.add_trace(
                go.Scatter(
                    x=view.index, y=series.reindex(view.index), name=label,
                    line=dict(width=1, color="#8c8c8c", dash=dash),
                    hovertemplate=_korean_hover(label, "원"), visible="legendonly",
                ),
                row=at["가격"], col=1,
            )
        env_low, _, env_up = ind.envelope(df["close"], 20, 10.0)
        for label, series in (("엔벨로프 상단 +10%", env_up), ("엔벨로프 하단 −10%", env_low)):
            fig.add_trace(
                go.Scatter(
                    x=view.index, y=series.reindex(view.index), name=label,
                    line=dict(width=1, color="#17becf", dash="dash"),
                    hovertemplate=_korean_hover(label, "원"), visible="legendonly",
                ),
                row=at["가격"], col=1,
            )

    # ------------------------------------------------------- 매물대 (오른쪽)
    edges, profile = ind.volume_profile(view, len(view), PROFILE_BINS)
    if profile.size:
        centers = (edges[:-1] + edges[1:]) / 2.0
        price = float(view["close"].iloc[-1])
        fig.add_trace(
            go.Bar(
                x=profile, y=centers, orientation="h", name="매물대",
                marker_color=[UP if c <= price else "#c9ccd4" for c in centers],
                hovertemplate="%{y:,.0f}원대 거래량 %{x:,.0f}주<extra></extra>",
                showlegend=False,
            ),
            row=1, col=2,
        )

    # ---------------------------------------------------------------- 2. 거래량
    fig.add_trace(
        go.Bar(
            x=view.index, y=view["volume"], name="거래량",
            marker_color=[
                UP if c >= o else DOWN for c, o in zip(view["close"], view["open"])
            ],
            hovertemplate=_korean_hover("거래량", "주"), showlegend=False,
        ),
        row=at["거래량"], col=1,
    )
    if len(df) >= 20:
        fig.add_trace(
            go.Scatter(
                x=view.index,
                y=df["volume"].rolling(20).mean().reindex(view.index),
                name="거래량 20일평균", line=dict(width=1, color="#444"),
                hovertemplate=_korean_hover("20일평균", "주"),
            ),
            row=at["거래량"], col=1,
        )

    # ---------------------------------------------------------------- 3. 수급
    if has_flows:
        for column, label, color in (
            ("foreign_net", "외국인 순매수", "#2ca02c"),
            ("inst_net", "기관 순매수", "#ff7f0e"),
        ):
            fig.add_trace(
                go.Bar(
                    x=view.index, y=pd.to_numeric(view[column], errors="coerce"),
                    name=label, marker_color=color, opacity=0.8,
                    hovertemplate=_korean_hover(label, "주"),
                ),
                row=at["수급"], col=1,
            )
        fig.add_hline(y=0, line_width=1, line_color="#999", row=at["수급"], col=1)

    # ---------------------------------------------------------------- 4. RSI
    if len(df) >= 15:
        fig.add_trace(
            go.Scatter(
                x=view.index, y=ind.rsi(df["close"], 14).reindex(view.index),
                name="RSI(14)", line=dict(width=1.2, color="#7f7f7f"),
                hovertemplate="RSI %{y:.1f}<extra></extra>",
            ),
            row=at["RSI"], col=1,
        )
        for level, label in ((70, "과매수 70"), (30, "과매도 30")):
            fig.add_hline(
                y=level, line_width=1, line_dash="dot", line_color="#bbb",
                annotation_text=label, annotation_font_size=10,
                row=at["RSI"], col=1,
            )

    # ---------------------------------------------------------------- 5. MACD
    if len(df) >= 35:
        macd_line, signal, hist = ind.macd(df["close"])
        fig.add_trace(
            go.Bar(
                x=view.index, y=hist.reindex(view.index), name="MACD 오실레이터",
                marker_color=[UP if v >= 0 else DOWN for v in hist.reindex(view.index)],
                hovertemplate="오실레이터 %{y:,.1f}<extra></extra>", showlegend=False,
            ),
            row=at["MACD"], col=1,
        )
        for label, series, color in (
            ("MACD", macd_line, "#1f77b4"), ("시그널", signal, "#ff7f0e")
        ):
            fig.add_trace(
                go.Scatter(
                    x=view.index, y=series.reindex(view.index), name=label,
                    line=dict(width=1.1, color=color),
                    hovertemplate=f"{label} %{{y:,.1f}}<extra></extra>",
                ),
                row=at["MACD"], col=1,
            )

    # ---------------------------------------------------------------- 축·범례
    fig.update_layout(
        title=dict(text=title, font=dict(size=15)),
        height=180 * len(rows) + 120,
        margin=dict(l=10, r=10, t=70, b=10),
        xaxis_rangeslider_visible=False,
        barmode="relative", bargap=0.1,
        hovermode="x unified", dragmode="pan",
        legend=dict(orientation="h", yanchor="bottom", y=1.015, x=0, font=dict(size=11)),
        font=dict(size=11),
    )
    units = {"가격": "원", "거래량": "주", "수급": "주", "RSI": "", "MACD": ""}
    for name, row in at.items():
        fig.update_yaxes(
            title_text=f"{name}({units[name]})" if units[name] else name,
            title_font_size=10, row=row, col=1,
        )
    fig.update_yaxes(range=[0, 100], row=at["RSI"], col=1)
    fig.update_xaxes(title_text="", showticklabels=False, row=1, col=2)
    fig.update_yaxes(showticklabels=False, row=1, col=2)
    fig.update_xaxes(
        title_text="날짜", title_font_size=10,
        rangebreaks=[dict(bounds=["sat", "mon"])], row=len(rows), col=1,
    )
    return fig


def open_in_browser(df: pd.DataFrame, title: str) -> str:
    """차트를 임시 HTML로 써서 기본 브라우저로 연다. 파일 경로를 돌려준다."""
    import tempfile
    import webbrowser

    safe = "".join(ch for ch in title.split()[0] if ch.isalnum()) or "chart"
    path = Path(tempfile.gettempdir()) / f"chartfinder_{safe}.html"
    candle_chart(df, title).write_html(
        str(path),
        include_plotlyjs="cdn",
        config={"displaylogo": False, "scrollZoom": True, "locale": "ko"},
    )
    webbrowser.open(path.as_uri())
    return str(path)
