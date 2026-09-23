"""초보자용 화면.

조건·파라미터·가중치를 전부 감추고 '무엇을 찾을지'만 고르게 한다.
지표 이름을 몰라도 쓸 수 있는 것이 목표다.

실행: chartfinder-ui   (고급 화면은 chartfinder-ui-advanced)
"""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chartfinder import cache, presets as presets_mod

PRESET_DIR = Path("presets")
MARKETS = (("kr", "한국 주식"), ("us", "미국 주식"), ("demo", "연습용 (가짜 데이터)"))
COLUMNS = ("순위", "종목명", "종목코드", "현재가", "등락", "적합도")
#: 수집 기간 (년). 초보자에게 물어볼 값이 아니라 고정한다.
YEARS = 2


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("주식 찾기")
        self.geometry("1000x760")
        self.minsize(860, 640)

        self.queue: queue.Queue = queue.Queue()
        self.busy = False
        self.result = None
        self.market = tk.StringVar(value="kr")
        self.chosen = tk.StringVar()
        self.presets: dict[str, presets_mod.Preset] = {}

        self._build()
        self._refresh_data_status()
        self.after(100, self._drain_queue)

    # ------------------------------------------------------------------ 화면
    def _build(self) -> None:
        header = ttk.Frame(self, padding=(16, 14, 16, 6))
        header.pack(fill="x")
        ttk.Label(header, text="주식 찾기", font=("", 20, "bold")).pack(anchor="w")
        ttk.Label(
            header,
            text="찾고 싶은 종류를 고르고 버튼만 누르세요. 조건은 알아서 적용됩니다.",
            foreground="#555",
        ).pack(anchor="w")

        body = ttk.Frame(self, padding=(16, 0, 16, 0))
        body.pack(fill="both", expand=True)

        self._build_market(body)
        self._build_strategies(body)
        self._build_action(body)
        self._build_results(body)
        self._build_status()

    def _build_market(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text=" 1단계 · 어느 시장에서 찾을까요? ", padding=10)
        box.pack(fill="x", pady=(8, 6))

        row = ttk.Frame(box)
        row.pack(fill="x")
        for code, label in MARKETS:
            ttk.Radiobutton(
                row, text=label, value=code, variable=self.market,
                command=self._refresh_data_status,
            ).pack(side="left", padx=(0, 18))

        self.data_status = ttk.Label(box, text="", foreground="#555")
        self.data_status.pack(anchor="w", pady=(8, 0))

    def _build_strategies(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text=" 2단계 · 어떤 종목을 찾을까요? ", padding=10)
        box.pack(fill="x", pady=6)

        loaded = presets_mod.load_all(PRESET_DIR)
        if not loaded:
            ttk.Label(box, text="전략 파일(presets 폴더)을 찾을 수 없습니다.").pack(anchor="w")
            return

        for index, (path, preset) in enumerate(loaded):
            self.presets[path.name] = preset
            if index == 0:
                self.chosen.set(path.name)

            row = ttk.Frame(box)
            row.pack(fill="x", pady=2)
            ttk.Radiobutton(
                row, text=preset.name, value=path.name, variable=self.chosen,
            ).pack(side="left")

            note = preset.description
            if preset.needs_flows:
                note += "   [외국인·기관 데이터 필요]"
            if preset.needs_fundamentals:
                note += "   [실적 데이터 필요]"
            ttk.Label(row, text=note, foreground="#666").pack(side="left", padx=(10, 0))

    def _build_action(self, parent: ttk.Frame) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(10, 4))

        ttk.Label(row, text=" 3단계 · ", font=("", 10, "bold")).pack(side="left")
        self.find_button = ttk.Button(row, text="종목 찾기", command=self.on_find)
        self.find_button.pack(side="left")
        ttk.Button(row, text="데이터 받기 / 새로고침", command=self.on_update).pack(
            side="left", padx=6
        )
        ttk.Button(row, text="결과 저장", command=self.on_save).pack(side="left")
        ttk.Button(row, text="고급 화면", command=self.on_advanced).pack(side="right")

    def _build_results(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text=" 결과 ", padding=8)
        box.pack(fill="both", expand=True, pady=(6, 8))

        # 표와 스크롤바는 한 줄에, 안내 문구는 그 아래에 둔다
        table = ttk.Frame(box)
        table.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(table, columns=COLUMNS, show="headings", height=14)
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
            box,
            text="줄을 두 번 클릭하면 차트가 열립니다. · 적합도는 조건에 얼마나 가까운지를 나타냅니다.",
            foreground="#666",
        ).pack(anchor="w", pady=(6, 0))

    def _build_status(self) -> None:
        bar = ttk.Frame(self, padding=(16, 0, 16, 12))
        bar.pack(fill="x")
        self.status = tk.StringVar(value="준비됨")
        ttk.Label(bar, textvariable=self.status, foreground="#444").pack(side="left")
        self.progress = ttk.Progressbar(bar, mode="determinate", length=240)
        self.progress.pack(side="right")

    # ------------------------------------------------------------------ 상태
    @property
    def preset(self) -> presets_mod.Preset | None:
        return self.presets.get(self.chosen.get())

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

    def _missing_data(self, preset: presets_mod.Preset) -> list[str]:
        """고른 전략에 필요한데 아직 없는 데이터."""
        market = self.market.get()
        missing = []
        if not cache.stats(market)["symbols"]:
            missing.append("시세")
        if preset.needs_flows and not cache.has_flows(market):
            missing.append("외국인·기관")
        if preset.needs_fundamentals and not cache.has_fundamentals(market):
            missing.append("실적")
        return missing

    # ------------------------------------------------------------------ 작업
    def run_worker(self, work: Callable[[], object], done: Callable[[object], None]) -> None:
        if self.busy:
            messagebox.showinfo("잠시만요", "앞의 작업이 끝날 때까지 기다려 주세요.")
            return
        self.busy = True
        self.progress["value"] = 0

        def target() -> None:
            try:
                self.queue.put(("done", work(), done))
            except Exception as exc:
                self.queue.put(("error", exc, done))

        threading.Thread(target=target, daemon=True).start()

    def _drain_queue(self) -> None:
        while not self.queue.empty():
            kind, payload, done = self.queue.get()
            if kind == "progress":
                current, total, text = payload
                self.progress["maximum"] = max(total, 1)
                self.progress["value"] = current
                self.status.set(text)
            elif kind == "done":
                self.busy = False
                done(payload)
            elif kind == "error":
                self.busy = False
                self.status.set("문제가 생겼습니다")
                messagebox.showerror("문제가 생겼습니다", str(payload))
        self.after(100, self._drain_queue)

    def report(self, current: int, total: int, text: str) -> None:
        self.queue.put(("progress", (current, total, text), None))

    # ------------------------------------------------------------------ 동작
    def on_update(self, after: Callable[[], None] | None = None) -> None:
        preset = self.preset
        market = self.market.get()
        universe = preset.universe if preset and preset.market == market else None
        flows = bool(preset and preset.needs_flows)
        fundamentals = bool(preset and preset.needs_fundamentals)

        def work():
            kwargs = {"universe": universe} if universe else {}
            stats = cache.update(
                market, years=YEARS, flows=flows,
                progress=lambda done, total, sym: self.report(
                    done, total, f"시세 받는 중 {done}/{total}"
                ),
                **kwargs,
            )
            if fundamentals:
                fund = cache.update_fundamentals(
                    market,
                    progress=lambda done, total, sym: self.report(
                        done, total, f"실적 받는 중 {done}/{total}"
                    ),
                    **kwargs,
                )
                stats["fundamentals"] = fund["updated"]
            return stats

        def finish(stats):
            self.progress["value"] = self.progress["maximum"]
            self.status.set(f"데이터 준비 완료 ({stats['updated'] + stats['skipped']}종목)")
            self._refresh_data_status()
            if after:
                after()

        self.status.set("데이터를 받는 중입니다. 처음에는 오래 걸릴 수 있습니다…")
        self.run_worker(work, finish)

    def on_find(self) -> None:
        preset = self.preset
        if preset is None:
            messagebox.showwarning("선택 필요", "찾을 종목 종류를 골라주세요.")
            return

        missing = self._missing_data(preset)
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
        universe = preset.universe if preset.market == market else None

        def work():
            from chartfinder.screener import screen

            tickers = cache.get_tickers(market, universe) if universe else None
            return screen(
                market, preset.conditions, tickers=tickers, top=50,
                progress=lambda done, total: self.report(done, total, f"살펴보는 중 {done}/{total}"),
            )

        self.status.set(f"'{preset.name}' 조건으로 찾는 중…")
        self.run_worker(work, self._show_result)

    def _show_result(self, result) -> None:
        self.tree.delete(*self.tree.get_children())
        self.result = result
        self.progress["value"] = self.progress["maximum"]

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
