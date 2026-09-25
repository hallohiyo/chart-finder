"""커맨드라인 인터페이스."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn
from rich.table import Table

from . import cache, presets as presets_mod
from .conditions import all_conditions, by_category, get as get_condition
from .datasource import MARKETS, default_universe, universes
from .screener import ConditionSpec, screen, unscored_conditions

app = typer.Typer(
    add_completion=False,
    help="차트 조건에 가장 근접한 종목을 찾아주는 스크리너 (한국/미국 시장)",
)
console = Console()


def _resolve_specs(
    cond: list[str], preset: Optional[Path], market: Optional[str], universe: Optional[str]
) -> tuple[list[ConditionSpec], str, str]:
    """-c / --preset 을 조건 목록으로 풀고 시장·유니버스를 정한다."""
    specs: list[ConditionSpec] = []
    if preset:
        loaded = presets_mod.load(preset)
        specs = list(loaded.conditions)
        market = market or loaded.market
        universe = universe or loaded.universe
        console.print(f"[dim]프리셋: {loaded.name}[/]")
    specs += [ConditionSpec.parse(text) for text in cond]

    if not specs:
        console.print("[red]조건이 없습니다.[/] -c 또는 --preset 을 지정하세요. "
                      "(목록: chartfinder conditions)")
        raise typer.Exit(code=1)

    market = market or "kr"
    return specs, market, _resolve_universe(market, universe)


def _resolve_universe(market: str, universe: str | None) -> str:
    """유니버스를 시장별 기본값으로 채우고, 잘못된 값이면 바로 알려준다."""
    allowed = universes(market)
    if universe is None:
        return default_universe(market)
    if universe.lower() not in allowed:
        console.print(f"[red]{market} 시장에 없는 유니버스: {universe}[/] (가능: {', '.join(allowed)})")
        raise typer.Exit(code=1)
    return universe.lower()


@app.command("conditions")
def list_conditions(
    category: Optional[str] = typer.Option(None, "--category", "-g", help="카테고리 필터"),
    key: Optional[str] = typer.Option(None, "--key", "-k", help="조건 하나의 상세 보기"),
) -> None:
    """사용 가능한 조건과 파라미터를 보여준다."""
    if key:
        cond = get_condition(key)
        console.print(f"[bold cyan]{cond.key}[/] — {cond.label}  ([dim]{cond.category}[/])")
        console.print(f"  {cond.description}\n")
        table = Table("파라미터", "설명", "타입", "기본값", "범위")
        for p in cond.params:
            rng = "-" if p.min is None and p.max is None else f"{p.min} ~ {p.max}"
            table.add_row(p.name, p.label, p.type, str(p.default), rng)
        console.print(table)
        console.print(f"\n[dim]예시:[/] chartfinder scan -c "
                      f"{cond.key}:{','.join(f'{p.name}={p.default}' for p in cond.params)}")
        return

    for cat, conds in by_category().items():
        if category and cat != category:
            continue
        table = Table(title=f"[bold]{cat}[/]", title_justify="left", show_lines=False)
        table.add_column("키", style="cyan", no_wrap=True)
        table.add_column("이름")
        table.add_column("파라미터 (기본값)", style="dim")
        for cond in conds:
            params = ", ".join(f"{p.name}={p.default}" for p in cond.params) or "-"
            table.add_row(cond.key, cond.label, params)
        console.print(table)
    console.print(f"[dim]총 {len(all_conditions())}개. 상세: chartfinder conditions -k <키>[/]")


@app.command("update")
def update_cache(
    market: str = typer.Option("kr", "--market", "-m", help=f"시장 {MARKETS}"),
    universe: Optional[str] = typer.Option(None, "--universe", "-u", help="생략하면 시장별 기본값"),
    years: float = typer.Option(2.0, "--years", "-y", help="받아올 과거 기간(년)"),
    force: bool = typer.Option(False, "--force", help="캐시를 무시하고 전체 재수집"),
    flows: bool = typer.Option(
        False, "--flows",
        help="외국인·기관 순매수도 함께 수집 (한국 시장 전용, 종목당 요청이 1회 늘어 느려짐)",
    ),
    fundamentals: bool = typer.Option(
        False, "--fundamentals",
        help="재무 데이터(매출·영업이익·ROE 등)도 함께 수집. 종목당 1회 요청이라 느리다",
    ),
    limit: Optional[int] = typer.Option(None, "--limit", help="앞에서 N종목만 (시험용)"),
) -> None:
    """일봉 데이터를 내려받아 로컬 캐시를 갱신한다."""
    universe = _resolve_universe(market, universe)
    tickers = cache.get_tickers(market, universe, refresh=force)
    symbols = [t.symbol for t in tickers][:limit] if limit else [t.symbol for t in tickers]
    console.print(f"[bold]{market.upper()}/{universe}[/] 종목 {len(symbols)}개 갱신 시작")

    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
        BarColumn(), TextColumn("{task.completed}/{task.total}"), TimeRemainingColumn(),
        console=console, redirect_stdout=False, redirect_stderr=False,
    ) as bar:
        task = bar.add_task("수집 중", total=max(len(symbols), 1))

        def on_progress(done: int, total: int, symbol: str) -> None:
            bar.update(task, completed=done, total=max(total, 1), description=f"수집 중 {symbol}")

        stats = cache.update(
            market, universe, years=years, symbols=symbols, force=force,
            flows=flows, progress=on_progress,
        )
        bar.update(task, completed=bar.tasks[0].total)

    summary = f"[green]완료[/] 갱신 {stats['updated']} · 최신 {stats['skipped']} · 실패 {stats['failed']}"
    if flows:
        summary += f" · 수급 {stats['flows']}"
        if stats.get("flow_source"):
            summary += f" [dim]({stats['flow_source']})[/]"
    console.print(summary)

    # 수급 0건 자체는 '받을 게 없었다'는 뜻일 수 있으므로, 실제 오류가 있을 때만 알린다
    if fundamentals:
        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            BarColumn(), TextColumn("{task.completed}/{task.total}"), TimeRemainingColumn(),
            console=console, redirect_stdout=False, redirect_stderr=False,
        ) as bar:
            task = bar.add_task("재무 수집", total=max(len(symbols), 1))
            fund_stats = cache.update_fundamentals(
                market, universe, symbols=symbols, force=force,
                progress=lambda done, total, sym: bar.update(
                    task, completed=done, total=max(total, 1), description=f"재무 수집 {sym}"
                ),
            )
        fund_summary = (
            f"[green]재무[/] 갱신 {fund_stats['updated']} · 최신 {fund_stats['skipped']} "
            f"· 실패 {fund_stats['failed']}"
        )
        if fund_stats.get("source"):
            fund_summary += f" [dim]({fund_stats['source']})[/]"
        console.print(fund_summary)
        if fund_stats.get("error"):
            console.print(f"[yellow]재무 수집 오류:[/] {fund_stats['error']}")

    if flows and stats.get("flow_error"):
        console.print(f"[yellow]수급을 받지 못한 종목이 있습니다:[/] {stats['flow_error']}")
    elif flows and not stats["flows"] and not stats["updated"]:
        console.print("[dim]수급은 이미 최신입니다.[/]")


@app.command("status")
def show_status(
    market: str = typer.Option("kr", "--market", "-m", help=f"시장 {MARKETS}"),
) -> None:
    """캐시 상태를 보여준다."""
    info = cache.stats(market)
    console.print(f"캐시 위치 : {cache.cache_home()}")
    console.print(f"시장      : {market}")
    console.print(f"종목 수   : {info['symbols']}")
    console.print(f"최근 일자 : {info['latest'] or '-'}")
    console.print(f"용량      : {info['size_mb']} MB")


@app.command("doctor")
def doctor(
    market: str = typer.Option("kr", "--market", "-m", help=f"시장 {MARKETS}"),
    symbol: Optional[str] = typer.Option(None, "--symbol", "-s", help="확인할 종목 (생략 시 첫 종목)"),
    days: int = typer.Option(30, "--days", help="조회할 기간(일)"),
) -> None:
    """데이터 수집이 어디서 막히는지 단계별로 보여준다."""
    import traceback
    from datetime import date, timedelta

    from .datasource import get_source

    end = date.today()
    start = end - timedelta(days=days)

    def step(label: str, fn):
        try:
            value = fn()
        except Exception as exc:
            console.print(f"[red]✗[/] {label}: {type(exc).__name__}: {exc}")
            frames = traceback.format_exc().strip().splitlines()
            console.print(f"[dim]   {frames[-3].strip() if len(frames) > 2 else ''}[/]")
            return None
        console.print(f"[green]✓[/] {label}")
        return value

    console.print(f"[bold]{market}[/] · {start} ~ {end}\n")

    source = step("데이터 소스 생성", lambda: get_source(market))
    if source is None:
        raise typer.Exit(code=1)

    universe = default_universe(market)
    tickers = step(f"종목 목록 ({universe})", lambda: source.list_tickers(universe))
    if tickers:
        console.print(f"   {len(tickers)}종목 · 예: {tickers[0].symbol} {tickers[0].name}")
        symbol = symbol or tickers[0].symbol

    if not symbol:
        raise typer.Exit(code=1)

    ohlcv = step(f"시세 조회 ({symbol})", lambda: source.fetch_ohlcv(symbol, start, end))
    if ohlcv is not None and not ohlcv.empty:
        console.print(f"   {len(ohlcv)}행 · {ohlcv.index[0].date()} ~ {ohlcv.index[-1].date()}")
        console.print(f"   컬럼: {list(ohlcv.columns)}")

    if not getattr(source, "supports_flows", False):
        console.print(f"[dim]·[/] 수급: {market} 시장은 지원하지 않습니다.")
        return

    # 정규화 전 원본 응답을 그대로 보여준다 (형식이 바뀌었는지 확인용)
    if market != "kr":
        flows = step(f"수급 조회 ({symbol})", lambda: source.fetch_flows(symbol, start, end))
        if flows is not None and not flows.empty:
            console.print(f"   {len(flows)}행 · 컬럼: {list(flows.columns)}")
        return

    def raw_flows():
        from pykrx import stock

        return stock.get_market_trading_volume_by_date(
            start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), symbol
        )

    raw = step(f"수급 · KRX 원본 응답 ({symbol})", raw_flows)
    if raw is not None:
        console.print(f"   {len(raw)}행 · 컬럼: {list(raw.columns)}")
        if len(raw):
            console.print(f"[dim]{raw.tail(3)}[/]")
        else:
            console.print("[yellow]   KRX 응답이 비어 있습니다 (차단·엔드포인트 변경 가능)[/]")

    def naver_flows_call():
        from .datasource import naver_flows

        return naver_flows.fetch(symbol, start, end)

    naver = step(f"수급 · 네이버 ({symbol})", naver_flows_call)
    if naver is not None:
        console.print(f"   {len(naver)}행 · 컬럼: {list(naver.columns)}")
        if len(naver):
            console.print(f"[dim]{naver.tail(3)}[/]")
        else:
            console.print("[yellow]   네이버 응답도 비어 있습니다[/]")

    if naver is None or naver.empty:
        def naver_diagnose():
            from .datasource import naver_flows

            return naver_flows.diagnose(symbol)

        info = step("네이버 응답 진단", naver_diagnose)
        if info:
            for key in ("status", "bytes", "declared_encoding", "decoded_with",
                        "has_marker", "parsed_rows"):
                if key in info:
                    console.print(f"   {key}: {info[key]}")
            tables = info.get("tables")
            if isinstance(tables, list):
                for i, cols in enumerate(tables[:5]):
                    console.print(f"   표{i}: {cols[:9]}")
            else:
                console.print(f"   표 파싱: {tables}")
            console.print(f"[dim]   본문: {str(info.get('snippet'))[:200]}[/]")

    def api_probe():
        from .datasource import naver_api

        return naver_api.probe(symbol)

    if naver is None or naver.empty:
        report = step("네이버 JSON API 탐색", api_probe)
        if report:
            for url, result in report.items():
                console.print(f"   [cyan]{url}[/]")
                console.print(f"     {str(result)[:400]}")

    flows = step(f"수급 최종 ({symbol})", lambda: source.fetch_flows(symbol, start, end))
    if flows is not None and not flows.empty:
        console.print(f"   {len(flows)}행 · 컬럼: {list(flows.columns)}")
        console.print(f"[dim]{flows.tail(3)}[/]")

    _check_fundamentals(source, market, symbol, step)


def _print_raw_finance_titles(symbol: str, step) -> None:
    """네이버가 실제로 주는 재무 항목 이름들. 아직 못 쓰는 항목을 찾기 위한 것."""

    def titles():
        from .datasource import naver_api

        _, data = naver_api.try_paths(symbol, naver_api.FINANCE_PATHS, kind="finance")
        return [
            str(record.get("title")) for record in naver_api.find_records(data)
            if record.get("title")
        ]

    names = step("재무 · 응답이 주는 항목 전체", titles)
    if names:
        console.print(f"   {', '.join(names)}")


def _check_dart(symbol: str, step) -> None:
    """영업활동현금흐름은 네이버가 주지 않아 DART 를 쓴다."""
    from .datasource import dart

    if not dart.enabled():
        console.print(
            "[dim]·[/] 영업현금흐름: DART 키가 없어 건너뜁니다. "
            "opendart.fss.or.kr 에서 무료 발급 후 DART_API_KEY 환경변수에 넣으면 채워집니다."
        )
        return

    flows = step(f"영업현금흐름 · DART ({symbol})", lambda: dart.fetch_cash_flow(symbol))
    if flows is not None and not flows.empty:
        console.print(f"[dim]{flows.to_string()}[/]")


def _check_fundamentals(source, market: str, symbol: str, step) -> None:
    if not getattr(source, "supports_fundamentals", False):
        console.print(f"[dim]·[/] 재무: {market} 시장은 지원하지 않습니다.")
        return

    fundamentals = step(f"재무 조회 ({symbol})", lambda: source.fetch_fundamentals(symbol))
    if fundamentals is not None and not fundamentals.empty:
        console.print(f"   {len(fundamentals)}개 연도 · {list(fundamentals.index)}")
        console.print(f"[dim]{fundamentals.round(1).to_string()}[/]")

        filled = [c for c in fundamentals.columns if fundamentals[c].notna().any()]
        missing = [c for c in fundamentals.columns if c not in filled]
        console.print(f"   [green]채워진 항목[/] {', '.join(filled) or '없음'}")
        if missing:
            console.print(f"   [yellow]빈 항목[/] {', '.join(missing)}")
        if market == "kr":
            _print_raw_finance_titles(symbol, step)
            _check_dart(symbol, step)
        return
    if market != "kr":
        return

    def fundamentals_diagnose():
        from .datasource import naver_fundamentals

        return naver_fundamentals.diagnose(symbol)

    info = step("재무 응답 진단", fundamentals_diagnose)
    if info:
        for key, value in info.items():
            console.print(f"   {key}: {str(value)[:300]}")


@app.command("scan")
def scan(
    cond: list[str] = typer.Option(
        [], "--cond", "-c",
        help="조건. 예: -c rsi_oversold:threshold=35,weight=2 (반복 지정 가능)",
    ),
    preset: list[Path] = typer.Option(
        [], "--preset", "-p",
        help="프리셋 YAML. 여러 번 지정하면 전략마다 따로 채점하고 가장 잘 맞는 점수로 순위를 매긴다",
    ),
    market: Optional[str] = typer.Option(None, "--market", "-m", help=f"시장 {MARKETS}"),
    universe: Optional[str] = typer.Option(None, "--universe", "-u", help="유니버스"),
    top: int = typer.Option(20, "--top", "-n", help="상위 N종목"),
    min_score: float = typer.Option(0.0, "--min-score", help="이 점수 미만 제외 (0~1)"),
    strict: bool = typer.Option(False, "--strict", help="모든 조건을 완전히 충족한 종목만"),
    csv: Optional[Path] = typer.Option(None, "--csv", help="결과를 CSV로 저장"),
    detail: bool = typer.Option(False, "--detail", "-d", help="조건별 점수도 표시"),
) -> None:
    """조건에 가장 근접한 종목을 찾는다."""
    if len(preset) > 1:
        _scan_multi(preset, market, universe, top, min_score, csv)
        return

    specs, market, universe = _resolve_specs(cond, preset[0] if preset else None,
                                             market, universe)

    tickers = cache.get_tickers(market, universe)
    console.print(
        f"[bold]{market.upper()}/{universe}[/] {len(tickers)}종목 · 조건 "
        + ", ".join(f"{s.key}(w={s.weight:g})" for s in specs)
    )

    with Progress(
        SpinnerColumn(), TextColumn("채점 중"), BarColumn(),
        TextColumn("{task.completed}/{task.total}"), console=console, transient=True,
        redirect_stdout=False, redirect_stderr=False,
    ) as bar:
        task = bar.add_task("scan", total=max(len(tickers), 1))
        result = screen(
            market, specs, tickers=tickers, strict=strict, min_score=min_score, top=top,
            progress=lambda done, total: bar.update(task, completed=done, total=max(total, 1)),
        )

    if result.empty:
        console.print("[yellow]조건에 맞는 종목이 없습니다.[/] "
                      "캐시가 비어 있다면 먼저 `chartfinder update` 를 실행하세요.")
        raise typer.Exit(code=0)

    all_score_cols = [c for c in result.columns if c.startswith("s_")]
    # 조건이 많으면 가로로 욱여넣지 않고 종목별 내역으로 따로 보여준다
    inline_scores = detail and len(all_score_cols) <= 5
    score_cols = all_score_cols if inline_scores else []

    table = Table(show_lines=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("종목", style="cyan", no_wrap=True)
    table.add_column("이름", no_wrap=True)
    table.add_column("점수", justify="right", style="bold green")
    table.add_column("충족", justify="right")
    table.add_column("종가", justify="right")
    table.add_column("등락%", justify="right")
    for col in score_cols:
        table.add_column(get_condition(col[2:]).label, justify="right", style="dim")

    for i, row in enumerate(result.itertuples(index=False), start=1):
        values = [
            str(i), row.symbol, row.name, f"{row.score:.3f}",
            f"{row.matched}/{len(specs)}", f"{row.close:,.2f}",
            f"[red]{row.chg_pct:+.2f}[/]" if row.chg_pct < 0 else f"[green]{row.chg_pct:+.2f}[/]",
        ]
        values += [f"{getattr(row, col):.2f}" for col in score_cols]
        table.add_row(*values)
    console.print(table)

    if detail and not inline_scores:
        _print_breakdown(result, all_score_cols)

    unscored = unscored_conditions(result, specs)
    if unscored:
        labels = ", ".join(get_condition(key).label for key in unscored)
        console.print(
            f"[yellow]채점되지 않은 조건[/] {labels}"
            " [dim]— 해당 데이터를 받지 않았을 수 있습니다 (--flows / --fundamentals)[/]"
        )

    if csv:
        csv.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(csv, index=False, encoding="utf-8-sig")
        console.print(f"[green]저장[/] {csv}")


def _scan_multi(
    paths: list[Path], market: Optional[str], universe: Optional[str],
    top: int, min_score: float, csv: Optional[Path],
) -> None:
    """전략마다 따로 채점한다. 평균내면 서로 반대인 조건이 섞여 희석된다."""
    from .screener import screen_multi

    loaded = [(path, presets_mod.load(path)) for path in paths]
    strategies = {preset.name.split(" — ")[0]: preset.conditions for _, preset in loaded}
    market = market or loaded[0][1].market
    universe = _resolve_universe(market, universe or loaded[0][1].universe)

    tickers = cache.get_tickers(market, universe)
    console.print(
        f"[bold]{market.upper()}/{universe}[/] {len(tickers)}종목 · 전략 {len(strategies)}개 "
        + " · ".join(f"{name}({len(specs)})" for name, specs in strategies.items())
    )

    with Progress(
        SpinnerColumn(), TextColumn("채점 중"), BarColumn(),
        TextColumn("{task.completed}/{task.total}"), console=console, transient=True,
        redirect_stdout=False, redirect_stderr=False,
    ) as bar:
        task = bar.add_task("scan", total=max(len(tickers), 1))
        result = screen_multi(
            market, strategies, tickers=tickers, top=top, min_score=min_score,
            progress=lambda done, total: bar.update(task, completed=done, total=max(total, 1)),
        )

    if result.empty:
        console.print("[yellow]조건에 맞는 종목이 없습니다.[/]")
        return

    table = Table(show_lines=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("종목", style="cyan", no_wrap=True)
    table.add_column("이름", no_wrap=True)
    table.add_column("적합도", justify="right", style="bold green")
    table.add_column("맞는 전략")
    table.add_column("종가", justify="right")
    table.add_column("등락%", justify="right")

    # 전략 이름에 공백이 있어 itertuples 는 컬럼명을 바꿔버린다
    records = result.to_dict("records")
    for i, row in enumerate(records, start=1):
        change = row["chg_pct"]
        table.add_row(
            str(i), row["symbol"], row["name"], f"{row['score']:.3f}", row["strategy"],
            f"{row['close']:,.2f}",
            f"[red]{change:+.2f}[/]" if change < 0 else f"[green]{change:+.2f}[/]",
        )
    console.print(table)

    # 전략별 점수는 열로 넣으면 좁은 터미널에서 뭉개지므로 줄로 푼다
    console.print()
    for i, row in enumerate(records, start=1):
        scores = " · ".join(
            f"{name} {row[f'p_{name}']:.2f}" for name in strategies
        )
        console.print(f"[dim]{i:>2}[/] [cyan]{row['symbol']}[/] {row['name']}")
        console.print(f"     {scores}")
    console.print(
        "[dim]적합도는 여러 전략의 평균이 아니라 가장 잘 맞는 전략 하나의 점수입니다.[/]"
    )

    if csv:
        csv.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(csv, index=False, encoding="utf-8-sig")
        console.print(f"[green]저장[/] {csv}")


def _print_breakdown(result, score_cols: list[str]) -> None:
    """종목별로 어떤 조건을 충족했고 어디서 깎였는지 풀어서 보여준다."""
    console.print()
    for i, row in enumerate(result.itertuples(index=False), start=1):
        scores = sorted(
            ((get_condition(col[2:]).label, getattr(row, col)) for col in score_cols),
            key=lambda pair: pair[1], reverse=True,
        )
        met = [label for label, value in scores if value >= 0.999]
        partial = [(label, value) for label, value in scores if 0 < value < 0.999]
        missed = [label for label, value in scores if value <= 0]

        console.print(
            f"[dim]{i:>2}[/] [cyan]{row.symbol}[/] {row.name} · "
            f"[bold green]{row.score:.3f}[/] ({row.matched}/{len(score_cols)})"
        )
        if met:
            console.print(f"     [green]충족[/] {' · '.join(met)}")
        if partial:
            console.print(
                "     [yellow]근접[/] "
                + " · ".join(f"{label} {value:.2f}" for label, value in partial)
            )
        if missed:
            console.print(f"     [dim]미달 {' · '.join(missed)}[/]")



@app.command("backtest")
def backtest(
    cond: list[str] = typer.Option([], "--cond", "-c", help="조건 (scan 과 같은 문법)"),
    preset: Optional[Path] = typer.Option(None, "--preset", "-p", help="프리셋 YAML (하나만)"),
    market: Optional[str] = typer.Option(None, "--market", "-m", help=f"시장 {MARKETS}"),
    universe: Optional[str] = typer.Option(None, "--universe", "-u", help="유니버스"),
    horizon: int = typer.Option(20, "--horizon", "-h", help="이후 수익률을 볼 영업일 수"),
    dates: int = typer.Option(10, "--dates", "-d", help="기준일 개수"),
    every: int = typer.Option(10, "--every", help="기준일 간격 (영업일)"),
    top: int = typer.Option(30, "--top", "-n", help="상위 몇 종목을 뽑아 볼지"),
    limit: Optional[int] = typer.Option(None, "--limit", help="앞에서 N종목만 (빠른 확인용)"),
) -> None:
    """과거 시점에서 채점하고 이후 수익률을 확인한다.

    기준일까지의 데이터만 보고 점수를 매긴 뒤 그 뒤 수익률을 본다.
    점수가 높을수록 수익률이 좋아야 이 조건들이 의미가 있다.
    """
    from . import backtest as bt

    specs, market, universe = _resolve_specs(cond, preset, market, universe)
    tickers = cache.get_tickers(market, universe)
    if limit:
        tickers = tickers[:limit]

    # 기준일은 표본이 가장 긴 종목의 거래일에서 고른다
    reference = next(
        (df for df in (cache.load(market, t.symbol) for t in tickers[:50]) if df is not None), None
    )
    if reference is None:
        console.print("[red]시세 캐시가 없습니다.[/] 먼저 `chartfinder update` 를 실행하세요.")
        raise typer.Exit(code=1)

    asof_dates = bt.trading_dates(reference, dates, every, horizon)
    if not asof_dates:
        console.print(
            f"[red]기준일을 잡을 수 없습니다.[/] 채점에 최소 {bt.MIN_BARS}봉 + "
            f"이후 {horizon}봉이 필요합니다. `update -y` 로 기간을 늘려주세요."
        )
        raise typer.Exit(code=1)

    console.print(
        f"[bold]{market.upper()}/{universe}[/] {len(tickers)}종목 · 조건 {len(specs)}개 · "
        f"기준일 {len(asof_dates)}개 ({asof_dates[0]} ~ {asof_dates[-1]}) · "
        f"이후 {horizon}영업일 · 상위 {top}종목"
    )

    with Progress(
        SpinnerColumn(), TextColumn("채점 중"), BarColumn(),
        TextColumn("{task.completed}/{task.total}"), TimeRemainingColumn(),
        console=console, transient=True, redirect_stdout=False, redirect_stderr=False,
    ) as bar:
        task = bar.add_task("backtest", total=max(len(tickers), 1))
        result = bt.run(
            market, specs, tickers, asof_dates, horizon=horizon, top=top,
            progress=lambda done, total: bar.update(task, completed=done, total=max(total, 1)),
        )

    if result.rows.empty:
        console.print("[yellow]표본이 없습니다.[/] 기간이 짧거나 캐시가 부족합니다.")
        raise typer.Exit(code=0)

    _print_backtest(result)


def _print_backtest(result) -> None:
    summary = result.summary()

    table = Table(title="기준일별", title_justify="left")
    for column in ("기준일", "종목수", "상위평균", "전체평균", "초과", "상위승률"):
        table.add_column(column, justify="right" if column != "기준일" else "left")
    for row in result.by_date().itertuples(index=False):
        excess = f"{row.초과:+.2f}%"
        table.add_row(
            str(row.asof), f"{row.종목수:,}", f"{row.상위평균:+.2f}%", f"{row.전체평균:+.2f}%",
            f"[green]{excess}[/]" if row.초과 > 0 else f"[red]{excess}[/]",
            f"{row.상위승률:.0f}%",
        )
    console.print(table)

    buckets = result.by_score_bucket()
    if not buckets.empty:
        bucket_table = Table(title="점수 구간별 (점수가 의미 있으면 아래로 갈수록 수익이 올라야 한다)",
                             title_justify="left")
        for column in ("점수 구간", "종목수", "평균수익", "중앙수익", "승률"):
            bucket_table.add_column(column, justify="right" if column != "점수 구간" else "left")
        for row in buckets.itertuples(index=False):
            bucket_table.add_row(
                row.구간, f"{row.종목수:,}", f"{row.평균수익:+.2f}%",
                f"{row.중앙수익:+.2f}%", f"{row.승률:.0f}%",
            )
        console.print(bucket_table)

    correlation = summary["점수-수익 상관"]
    noise = result.noise_level()
    if abs(correlation) < noise:
        verdict = (
            f"[yellow]잡음 범위(±{noise:.3f}) 안입니다 — 이 표본으로는 판단할 수 없습니다[/]"
        )
    elif correlation > 0:
        verdict = "[green]점수가 높을수록 수익이 좋았습니다[/]"
    else:
        verdict = "[red]점수가 높을수록 오히려 나빴습니다[/]"

    excess = summary["평균초과수익"]
    console.print(
        f"\n기준일 {summary['기준일수']:.0f}개 · 표본 {summary['표본수']:,.0f}건\n"
        f"상위 평균 {summary['상위평균수익']:+.2f}% · 전체 평균 {summary['전체평균수익']:+.2f}% · "
        f"[bold]초과 {excess:+.2f}%[/] (초과한 기준일 비율 {summary['초과승률']:.0f}%)\n"
        f"점수-수익 상관 {correlation:+.3f} → {verdict}"
    )
    if abs(correlation) >= noise and (correlation > 0) != (excess > 0):
        console.print(
            "[yellow]상관과 초과수익의 방향이 엇갈립니다.[/] 기준일을 늘려 다시 보세요."
        )
    console.print(
        "[dim]매매 비용·슬리피지는 반영하지 않았고, 상장폐지 종목이 빠져 있어 "
        "실제보다 낙관적일 수 있습니다.[/]"
    )


@app.command("presets")
def show_presets(
    folder: Path = typer.Option(Path("presets"), "--folder", "-f", help="프리셋 폴더"),
) -> None:
    """프리셋 목록을 보여준다."""
    paths = presets_mod.list_presets(folder)
    if not paths:
        console.print(f"[yellow]{folder} 에 프리셋이 없습니다.[/]")
        return
    table = Table("파일", "이름", "시장", "조건 수", "설명")
    for path in paths:
        preset = presets_mod.load(path)
        table.add_row(
            path.name, preset.name, f"{preset.market}/{preset.universe}",
            str(len(preset.conditions)), preset.description,
        )
    console.print(table)


if __name__ == "__main__":
    app()
