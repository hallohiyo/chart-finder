"""캔들차트 생성 (웹 UI와 데스크톱 UI가 공유)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import indicators as ind

#: 이동평균선과 색
MA_LINES = ((20, "#ff7f0e"), (60, "#2ca02c"), (120, "#9467bd"))


def candle_chart(df: pd.DataFrame, title: str, months: int = 12):
    """캔들 + 이동평균 + 거래량 차트. plotly Figure 를 돌려준다."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    view = df.tail(months * 21)
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.75, 0.25]
    )
    fig.add_trace(
        go.Candlestick(
            x=view.index, open=view["open"], high=view["high"],
            low=view["low"], close=view["close"], name="가격",
            increasing_line_color="#d62728", decreasing_line_color="#1f77b4",
        ),
        row=1, col=1,
    )
    for period, color in MA_LINES:
        if len(df) >= period:
            fig.add_trace(
                go.Scatter(
                    x=view.index, y=ind.sma(df["close"], period).reindex(view.index),
                    name=f"MA{period}", line=dict(width=1.2, color=color),
                ),
                row=1, col=1,
            )
    fig.add_trace(
        go.Bar(x=view.index, y=view["volume"], name="거래량", marker_color="#b0b7c3"),
        row=2, col=1,
    )
    fig.update_layout(
        title=title, height=560, margin=dict(l=10, r=10, t=40, b=10),
        xaxis_rangeslider_visible=False, showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    return fig


def open_in_browser(df: pd.DataFrame, title: str) -> str:
    """차트를 임시 HTML로 써서 기본 브라우저로 연다. 파일 경로를 돌려준다."""
    import tempfile
    import webbrowser

    path = Path(tempfile.gettempdir()) / f"chartfinder_{title.split()[0]}.html"
    candle_chart(df, title).write_html(str(path), include_plotlyjs="cdn")
    webbrowser.open(path.as_uri())
    return str(path)
