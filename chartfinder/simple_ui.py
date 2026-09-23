"""초보자용 화면.

조건·파라미터·가중치를 전부 감추고 '무엇을 찾을지'만 고르게 한다.
지표 이름을 몰라도 쓸 수 있는 것이 목표다.

실행: chartfinder-ui   (고급 화면은 chartfinder-ui-advanced)
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chartfinder import cache, presets as presets_mod

class Cancelled(Exception):
    """사용자가 취소를 눌렀을 때 수집 루프를 빠져나오기 위한 신호."""


def _duration(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    if seconds < 60:
        return f"{seconds}초"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}분 {seconds}초"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}시간 {minutes}분"


PRESET_DIR = Path("presets")
MARKETS = (("kr", "한국 주식"), ("us", "미국 주식"), ("demo", "연습용 (가짜 데이터)"))
COLUMNS = ("순위", "종목명", "종목코드", "현재가", "등락", "적합도")
#: 수집 기간 (년). 초보자에게 물어볼 값이 아니라 고정한다.
YEARS = 2


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("주식 찾기")
        self.geometry("1000x680")
        self.minsize(820, 560)

        self.queue: queue.Queue = queue.Queue()
        self.busy = False
        self.cancelled = False
        self.started_at = 0.0
        self.result = None
        self.market = tk.StringVar(value="kr")
        self.presets: dict[str, presets_mod.Preset] = {}
        self.checked: dict[str, tk.BooleanVar] = {}

        self._build()
        self._refresh_data_status()
        self.after(100, self._drain_queue)

    # ------------------------------------------------------------------ 화면
    def _build(self) -> None:
        header = ttk.Frame(self, padding=(14, 10, 14, 2))
        header.pack(fill="x")
        ttk.Label(header, text="주식 찾기", font=("", 16, "bold")).pack(side="left")
        ttk.Label(
            header, text="  시장과 전략을 고르고 '종목 찾기'를 누르세요.",
            foreground="#666",
        ).pack(side="left", padx=(8, 0))

        body = ttk.Frame(self, padding=(14, 0, 14, 0))
        body.pack(fill="both", expand=True)

        self._build_market(body)
        self._build_strategies(body)
        self._build_action(body)
        self._build_results(body)
        self._build_status()

    def _build_market(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text=" 1단계 · 시장 ", padding=(10, 6))
        box.pack(fill="x", pady=(6, 4))

        row = ttk.Frame(box)
        row.pack(fill="x")
        for code, label in MARKETS:
            ttk.Radiobutton(
                row, text=label, value=code, variable=self.market,
                command=self._refresh_data_status,
            ).pack(side="left", padx=(0, 14))

        # 데이터 상태는 같은 줄 오른쪽에 (세로 공간을 아낀다)
        self.data_status = ttk.Label(row, text="", foreground="#555")
        self.data_status.pack(side="right")

    def _build_strategies(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(
            parent, text=" 2단계 · 전략  (여러 개를 고르면 조건을 합칩니다) ", padding=(10, 6),
        )
        box.pack(fill="x", pady=4)

        loaded = presets_mod.load_all(PRESET_DIR)
        if not loaded:
            ttk.Label(box, text="전략 파일(presets 폴더)을 찾을 수 없습니다.").pack(anchor="w")
            return

        for index, (path, preset) in enumerate(loaded):
            self.presets[path.name] = preset
            variable = tk.BooleanVar(value=True)  # 기본은 전부 선택
            self.checked[path.name] = variable

            # 한 전략 = 한 줄. 설명은 같은 줄 회색 글씨로 (세로 공간을 아낀다)
            row = ttk.Frame(box)
            row.pack(fill="x")
            ttk.Checkbutton(
                row, text=preset.name, variable=variable, command=self._refresh_choice,
                width=34,
            ).pack(side="left")

            note = preset.description
            if preset.needs_flows:
                note += "  [수급 필요]"
            if preset.needs_fundamentals:
                note += "  [실적 필요]"
            ttk.Label(row, text=note, foreground="#666").pack(side="left")

        buttons = ttk.Frame(box)
        buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(buttons, text="전체 선택", width=10,
                   command=lambda: self._set_all(True)).pack(side="left")
        ttk.Button(buttons, text="전체 해제", width=10,
                   command=lambda: self._set_all(False)).pack(side="left", padx=4)
        self.choice_summary = ttk.Label(buttons, text="", foreground="#333")
        self.choice_summary.pack(side="left", padx=(10, 0))
        self._refresh_choice()

    def _set_all(self, value: bool) -> None:
        for variable in self.checked.values():
            variable.set(value)
        self._refresh_choice()

    def _build_action(self, parent: ttk.Frame) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(6, 2))

        ttk.Label(row, text="3단계 · ", font=("", 10, "bold")).pack(side="left")
        self.find_button = ttk.Button(row, text="종목 찾기", command=self.on_find)
        self.find_button.pack(side="left")
        ttk.Button(row, text="데이터 받기 / 새로고침", command=self.on_update).pack(
            side="left", padx=6
        )
        ttk.Button(row, text="결과 저장", command=self.on_save).pack(side="left")
        ttk.Button(row, text="고급 화면", command=self.on_advanced).pack(side="right")

    def _build_results(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text=" 결과 ", padding=(8, 4))
        box.pack(fill="both", expand=True, pady=(4, 4))

        # 표와 스크롤바는 한 줄에, 안내 문구는 그 아래에 둔다
        table = ttk.Frame(box)
        table.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(table, columns=COLUMNS, show="headings", height=8)
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)

        widths = {"순위": 50, "종목명": 220, "종목코드": 90, "현재가": 110, "등락": 80, "적합도": 90}
        for col in COLUMNS:
            self.tree.heading(col, text=col)
            self.tree.column(
                col, width=widths[col], anchor="w" if col == "종목명" else "center"
            )
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.on_open_chart)

        ttk.Label(
            box, text="줄을 두 번 클릭하면 차트가 열립니다. 적합도는 조건에 얼마나 가까운지입니다.",
            foreground="#666",
        ).pack(anchor="w", pady=(4, 0))

    def _build_status(self) -> None:
        bar = ttk.Frame(self, padding=(14, 0, 14, 8))
        bar.pack(fill="x")

        top = ttk.Frame(bar)
        top.pack(fill="x")
        self.status = tk.StringVar(value="준비됨")
        ttk.Label(top, textvariable=self.status, foreground="#333").pack(side="left")
        self.cancel_button = ttk.Button(top, text="취소", command=self.on_cancel)
        # 작업 중에만 보인다

        self.progress = ttk.Progressbar(bar, mode="determinate")
        self.progress.pack(fill="x", pady=(4, 2))

        self.timing = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.timing, foreground="#666").pack(anchor="w")

    # ------------------------------------------------------------------ 상태
    @property
    def selected(self) -> list[presets_mod.Preset]:
        """체크된 전략들 (화면에 보이는 순서대로)."""
        return [self.presets[name] for name, var in self.checked.items() if var.get()]

    @property
    def conditions(self) -> list:
        """고른 전략들의 조건을 합친 것."""
        return presets_mod.merge(self.selected)

    def _refresh_choice(self) -> None:
        chosen = self.selected
        if not chosen:
            self.choice_summary.config(text="전략을 하나 이상 골라주세요.", foreground="#b00")
            return
        self.choice_summary.config(
            text=f"{self._choice_label(chosen)} · 합친 조건 {len(self.conditions)}개",
            foreground="#333",
        )

    def _choice_label(self, chosen: list[presets_mod.Preset]) -> str:
        """고른 전략을 한 줄에 들어갈 길이로 요약한다."""
        if len(chosen) == len(self.presets):
            return f"전체 {len(chosen)}개 전략"
        names = [preset.name.split(" — ")[0] for preset in chosen]
        if len(names) <= 3:
            return ", ".join(names)
        return f"{', '.join(names[:2])} 외 {len(names) - 2}개"

    def _refresh_data_status(self) -> None:
        market = self.market.get()
        info = cache.stats(market)
        if not info["symbols"]:
            self.data_status.config(
                text="아직 데이터가 없습니다. '데이터 받기'를 먼저 눌러주세요.",
                foreground="#b00",
            )
            return
        extras = []
        if cache.has_flows(market):
            extras.append("외국인·기관")
        if cache.has_fundamentals(market):
            extras.append("실적")
        suffix = f" · {' · '.join(extras)} 포함" if extras else ""
        self.data_status.config(
            text=f"{info['symbols']}종목 · 최근 {info['latest'] or '-'}{suffix}",
            foreground="#555",
        )

    def _missing_data(self, presets: list[presets_mod.Preset]) -> list[str]:
        """고른 전략들에 필요한데 아직 없는 데이터."""
        market = self.market.get()
        missing = []
        if not cache.stats(market)["symbols"]:
            missing.append("시세")
        if any(p.needs_flows for p in presets) and not cache.has_flows(market):
            missing.append("외국인·기관")
        if any(p.needs_fundamentals for p in presets) and not cache.has_fundamentals(market):
            missing.append("실적")
        return missing

    # ------------------------------------------------------------------ 작업
    def run_worker(self, work: Callable[[], object], done: Callable[[object], None]) -> None:
        if self.busy:
            messagebox.showinfo("잠시만요", "앞의 작업이 끝날 때까지 기다려 주세요.")
            return
        self.busy = True
        self.cancelled = False
        self.started_at = time.monotonic()
        self.progress["value"] = 0
        self.timing.set("")
        self.cancel_button.pack(side="right")

        def target() -> None:
            try:
                self.queue.put(("done", work(), done))
            except Cancelled:
                self.queue.put(("cancelled", None, done))
            except Exception as exc:
                self.queue.put(("error", exc, done))

        threading.Thread(target=target, daemon=True).start()

    def on_cancel(self) -> None:
        """다음 진행 보고 시점에 멈춘다. 이미 받은 데이터는 남는다."""
        if self.busy:
            self.cancelled = True
            self.status.set("멈추는 중…")

    def _drain_queue(self) -> None:
        while not self.queue.empty():
            kind, payload, done = self.queue.get()
            if kind == "progress":
                self._show_progress(*payload)
            elif kind == "done":
                self._finish()
                done(payload)
            elif kind == "cancelled":
                self._finish()
                self.status.set("취소했습니다. 지금까지 받은 데이터는 그대로 남아 있습니다.")
                self._refresh_data_status()
            elif kind == "error":
                self._finish()
                self.status.set("문제가 생겼습니다")
                messagebox.showerror("문제가 생겼습니다", str(payload))
        self.after(100, self._drain_queue)

    def _finish(self) -> None:
        self.busy = False
        self.cancel_button.pack_forget()

    def _show_progress(self, current: int, total: int, label: str) -> None:
        total = max(total, 1)
        self.progress["maximum"] = total
        self.progress["value"] = current
        percent = current / total * 100
        self.status.set(f"{label} · {current:,} / {total:,} ({percent:.0f}%)")
        self.timing.set(self._timing_text(current, total))

    def _timing_text(self, current: int, total: int) -> str:
        """경과 시간과 남은 시간 추정. 초반에는 추정을 내지 않는다."""
        elapsed = time.monotonic() - self.started_at
        text = f"경과 {_duration(elapsed)}"
        if current >= 5 and current < total:
            remaining = elapsed / current * (total - current)
            text += f" · 남은 시간 약 {_duration(remaining)}"
        return text

    def report(self, current: int, total: int, text: str) -> None:
        """작업 스레드에서 진행 상황을 알린다.

        취소를 눌렀다면 여기서 예외를 던져 수집 루프를 빠져나간다.
        """
        if self.cancelled:
            raise Cancelled()
        self.queue.put(("progress", (current, total, text), None))

    # ------------------------------------------------------------------ 동작
    def on_update(self, after: Callable[[], None] | None = None) -> None:
        chosen = self.selected
        market = self.market.get()
        universe = self._universe(chosen, market)
        flows = any(p.needs_flows for p in chosen)
        fundamentals = any(p.needs_fundamentals for p in chosen)

        def work():
            kwargs = {"universe": universe} if universe else {}
            stats = cache.update(
                market, years=YEARS, flows=flows,
                progress=lambda done, total, sym: self.report(done, total, "시세 받는 중"),
                **kwargs,
            )
            if fundamentals:
                fund = cache.update_fundamentals(
                    market,
                    progress=lambda done, total, sym: self.report(done, total, "실적 받는 중"),
                    **kwargs,
                )
                stats["fundamentals"] = fund["updated"]
            return stats

        def finish(stats):
            self.progress["value"] = self.progress["maximum"]
            done = stats["updated"] + stats["skipped"]
            self.status.set(f"데이터 준비 완료 · {done:,}종목")
            self.timing.set(f"걸린 시간 {_duration(time.monotonic() - self.started_at)}")
            self._refresh_data_status()
            if after:
                after()

        self.status.set("종목 목록을 확인하는 중입니다…")
        self.run_worker(work, finish)

    def _universe(self, chosen: list[presets_mod.Preset], market: str) -> str | None:
        """고른 전략들이 같은 유니버스를 가리키면 그것을, 아니면 기본값을 쓴다."""
        universes = {p.universe for p in chosen if p.market == market}
        return universes.pop() if len(universes) == 1 else None

    def on_find(self) -> None:
        chosen = self.selected
        if not chosen:
            messagebox.showwarning("선택 필요", "찾을 종목 종류를 하나 이상 골라주세요.")
            return

        missing = self._missing_data(chosen)
        if missing:
            answer = messagebox.askyesno(
                "데이터가 필요합니다",
                f"이 전략에는 {', '.join(missing)} 데이터가 필요합니다.\n"
                "지금 받을까요? (처음에는 수십 분 걸릴 수 있습니다)",
            )
            if answer:
                self.on_update(after=self.on_find)
            return

        market = self.market.get()
        universe = self._universe(chosen, market)
        conditions = self.conditions

        def work():
            from chartfinder.screener import screen

            tickers = cache.get_tickers(market, universe) if universe else None
            return screen(
                market, conditions, tickers=tickers, top=50,
                progress=lambda done, total: self.report(done, total, "종목 살펴보는 중"),
            )

        self.status.set(f"{self._choice_label(chosen)} · 조건 {len(conditions)}개로 찾는 중…")
        self.run_worker(work, self._show_result)

    def _show_result(self, result) -> None:
        self.tree.delete(*self.tree.get_children())
        self.result = result
        self.progress["value"] = self.progress["maximum"]
        self.timing.set(f"걸린 시간 {_duration(time.monotonic() - self.started_at)}")

        if result is None or result.empty:
            self.status.set("조건에 맞는 종목이 없습니다.")
            return

        for i, row in enumerate(result.itertuples(index=False), start=1):
            self.tree.insert(
                "", "end",
                values=(i, row.name, row.symbol, f"{row.close:,.0f}",
                        f"{row.chg_pct:+.2f}%", f"{row.score * 100:.0f}%"),
            )
        self.status.set(f"{len(result)}종목을 찾았습니다. 위에 있을수록 조건에 가깝습니다.")

    def on_open_chart(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        values = self.tree.item(selection[0], "values")
        name, symbol = values[1], values[2]
        df = cache.load(self.market.get(), symbol)
        if df is None or df.empty:
            messagebox.showwarning("데이터 없음", f"{symbol} 의 시세가 없습니다.")
            return
        try:
            from chartfinder.charts import open_in_browser

            open_in_browser(df, f"{symbol} {name}")
            self.status.set(f"{name} 차트를 브라우저에서 열었습니다.")
        except ImportError:
            messagebox.showinfo("설치 필요", "차트를 보려면 plotly 가 필요합니다.")

    def on_save(self) -> None:
        if self.result is None or self.result.empty:
            messagebox.showwarning("결과 없음", "먼저 종목을 찾아주세요.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("엑셀에서 열 수 있는 파일", "*.csv")],
            initialfile="찾은종목.csv",
        )
        if path:
            self.result.to_csv(path, index=False, encoding="utf-8-sig")
            self.status.set(f"저장했습니다: {path}")

    def on_advanced(self) -> None:
        """조건을 직접 만지고 싶을 때 여는 원래 화면."""
        from chartfinder.ui import App as AdvancedApp

        window = AdvancedApp()
        window.transient(self)


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
