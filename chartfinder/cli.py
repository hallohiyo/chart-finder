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
from .screener import ConditionSpec, screen

app = typer.Typer(
    add_completion=False,
    help="차트 조건에 가장 근접한 종목을 찾아주는 스크리너 (한국/미국 시장)",
)
console = Console()


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
        console=console,
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
    console.print(summary)

    # 수급 0건 자체는 '받을 게 없었다'는 뜻일 수 있으므로, 실제 오류가 있을 때만 알린다
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


@app.command("scan")
def scan(
    cond: list[str] = typer.Option(
        [], "--cond", "-c",
        help="조건. 예: -c rsi_oversold:threshold=35,weight=2 (반복 지정 가능)",
    ),
    preset: Optional[Path] = typer.Option(None, "--preset", "-p", help="프리셋 YAML 경로"),
    market: Optional[str] = typer.Option(None, "--market", "-m", help=f"시장 {MARKETS}"),
    universe: Optional[str] = typer.Option(None, "--universe", "-u", help="유니버스"),
    top: int = typer.Option(20, "--top", "-n", help="상위 N종목"),
    min_score: float = typer.Option(0.0, "--min-score", help="이 점수 미만 제외 (0~1)"),
    strict: bool = typer.Option(False, "--strict", help="모든 조건을 완전히 충족한 종목만"),
    csv: Optional[Path] = typer.Option(None, "--csv", help="결과를 CSV로 저장"),
    detail: bool = typer.Option(False, "--detail", "-d", help="조건별 점수도 표시"),
) -> None:
    """조건에 가장 근접한 종목을 찾는다."""
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
    universe = _resolve_universe(market, universe)

    tickers = cache.get_tickers(market, universe)
    console.print(
        f"[bold]{market.upper()}/{universe}[/] {len(tickers)}종목 · 조건 "
        + ", ".join(f"{s.key}(w={s.weight:g})" for s in specs)
    )

    with Progress(
        SpinnerColumn(), TextColumn("채점 중"), BarColumn(),
        TextColumn("{task.completed}/{task.total}"), console=console, transient=True,
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

    table = Table(show_lines=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("종목", style="cyan", no_wrap=True)
    table.add_column("이름")
    table.add_column("점수", justify="right", style="bold green")
    table.add_column("충족", justify="right")
    table.add_column("종가", justify="right")
    table.add_column("등락%", justify="right")
    score_cols = [c for c in result.columns if c.startswith("s_")] if detail else []
    for col in score_cols:
        table.add_column(col[2:], justify="right", style="dim")

    for i, row in enumerate(result.itertuples(index=False), start=1):
        values = [
            str(i), row.symbol, row.name, f"{row.score:.3f}",
            f"{row.matched}/{len(specs)}", f"{row.close:,.2f}",
            f"[red]{row.chg_pct:+.2f}[/]" if row.chg_pct < 0 else f"[green]{row.chg_pct:+.2f}[/]",
        ]
        values += [f"{getattr(row, col):.2f}" for col in score_cols]
        table.add_row(*values)
    console.print(table)

    if csv:
        csv.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(csv, index=False, encoding="utf-8-sig")
        console.print(f"[green]저장[/] {csv}")


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
